"""BaseAgent — core Claude tool_use reasoning loop.

All specialized agents (Order Ops, Customer Service, etc.) inherit from this.
The loop follows Anthropic's official pattern: call Claude with tools, process
ALL tool_use blocks, intercept with PolicyEngine, execute or block, feed results
back, repeat until end_turn or max_turns.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from src.agents.audit import AuditLogger
from src.agents.guardrails import PolicyEngine
from src.agents.tools import ToolRegistry
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of an agent run."""

    success: bool
    message: str
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    escalated: bool = False
    audit_id: str | None = None
    tokens_used: int = 0
    # "high", "medium", "low", or "unknown" — self-reported by the agent.
    # "unknown" is the fail-open default when the agent didn't tag its response;
    # we do NOT assume high confidence silently (that masks uncertainty and
    # trains downstream feedback to reinforce over-confident agents).
    confidence: str = "unknown"


CONFIDENCE_INSTRUCTIONS = """

CONFIDENCE RATING (required in your final response):
End every response with one of [CONFIDENCE: high], [CONFIDENCE: medium], or [CONFIDENCE: low].
- HIGH: You completed the task successfully with clear data. No ambiguity. You would make the same call in a review.
- MEDIUM: You completed the task but had to make at least one assumption or lacked some data. Say which.
- LOW: You are uncertain about your actions OR the data was contradictory / missing. Explain what you're unsure about.

Omitting the confidence tag is treated as 'unknown' and routed for review.
Be concise. Act first, explain only when needed. Do not narrate each step — just do it."""


class BaseAgent:
    """Core agent with Claude tool_use loop and guardrail interception.

    Subclasses set: name, system_prompt, and call _register_tools() to populate
    the ToolRegistry with domain-specific tools.
    """

    name: str = "base"
    system_prompt: str = "You are a helpful assistant."
    max_turns: int = 15
    max_tokens_per_turn: int = 1024  # Keep responses concise — agents should act, not explain

    def __init__(
        self,
        tools: ToolRegistry | None = None,
        policies: PolicyEngine | None = None,
    ):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.tools = tools or ToolRegistry()
        self.policies = policies or PolicyEngine()
        self.audit = AuditLogger()
        self._slack = SlackNotifier()

        # Register shared tools available to every agent before subclass hooks
        self._register_base_tools()

        # Let subclasses register their domain-specific tools
        self._register_tools()

    def _register_base_tools(self) -> None:
        """Tools every agent gets. Kept tiny on purpose — only genuinely
        agent-agnostic capabilities belong here. Today: self-memory lookup
        (Phase 13.4 llm-wiki applied to the agent's own recent history)."""
        self.tools.register(
            name="get_my_memory",
            description=(
                "Read-only. Return YOUR OWN compiled rolling memory note "
                "(last 7 days of your runs, outcomes, and auto-sent actions, "
                "distilled to ~400 words by a nightly compiler). Use this at "
                "the start of a run when prior context would help — e.g. "
                "a repeat customer, a recurring escalation pattern, a lifecycle "
                "trigger you've fired several times. Do NOT call for cold "
                "utility runs; that's just extra tokens. Returns {content, "
                "compiled_at, sample_count} or null if your memory hasn't "
                "been compiled yet."
            ),
            input_schema={"type": "object", "properties": {}},
            func=self._get_my_memory_wrapper,
            risk_tier=1,
        )

    async def _get_my_memory_wrapper(self) -> dict[str, Any] | None:
        from src.agents.memory import get_memory
        note = await get_memory("agent", self.name)
        if not note:
            return None
        return {
            "content": note.get("content"),
            "compiled_at": note.get("compiled_at"),
            "sample_count": note.get("sample_count"),
        }

    def _register_tools(self) -> None:
        """Override in subclasses to register domain-specific tools."""

    def _build_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Build the user prompt from task description and assembled context.

        Trims context to reduce token usage: removes null values, truncates
        long lists, and caps string fields.
        """
        parts = [f"## Task\n{task}"]
        if context:
            parts.append("## Context")
            for key, value in context.items():
                trimmed = self._trim_context_value(value)
                if isinstance(trimmed, (dict, list)):
                    parts.append(f"### {key}\n```json\n{json.dumps(trimmed, indent=2, default=str)}\n```")
                else:
                    parts.append(f"### {key}\n{trimmed}")
        return "\n\n".join(parts)

    @staticmethod
    def _trim_context_value(value: Any, max_list: int = 10, max_str: int = 500) -> Any:
        """Remove nulls, truncate lists and strings to keep context lean."""
        if value is None:
            return None
        if isinstance(value, str):
            return value[:max_str] if len(value) > max_str else value
        if isinstance(value, list):
            trimmed = [BaseAgent._trim_context_value(v) for v in value[:max_list]]
            if len(value) > max_list:
                trimmed.append(f"... and {len(value) - max_list} more")
            return trimmed
        if isinstance(value, dict):
            return {k: BaseAgent._trim_context_value(v) for k, v in value.items() if v is not None}
        return value

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text content from a Claude response."""
        texts = []
        for block in response.content:
            if hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts) if texts else ""

    @staticmethod
    def _extract_confidence(text: str) -> str:
        """Extract confidence level from agent's response text.

        Looks for [CONFIDENCE: high/medium/low] pattern. Returns 'unknown' if
        missing — we do not silently default to 'high'. An agent that forgets
        to tag confidence is not the same as an agent that is actually sure.
        """
        import re
        match = re.search(r"\[CONFIDENCE:\s*(high|medium|low)\]", text, re.IGNORECASE)
        if not match:
            logger.warning("Agent response missing [CONFIDENCE: …] tag; treating as 'unknown'")
            return "unknown"
        return match.group(1).lower()

    async def _escalate_to_slack(
        self,
        tool_block,
        decision,
        context: dict[str, Any],
    ) -> None:
        """Post an escalation message to Slack when a policy blocks a tool call."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":warning: Agent Escalation — {self.name}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tool:* `{tool_block.name}`"},
                    {"type": "mrkdwn", "text": f"*Policy:* {decision.policy_name}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Reason:* {decision.reason}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Input:*\n```{json.dumps(tool_block.input, indent=2, default=str)[:500]}```",
                },
            },
        ]
        try:
            await self._slack.send_blocks(blocks, text=f"Agent escalation: {self.name}")
        except Exception:
            logger.exception("Failed to send escalation to Slack")

    async def run(self, task: str, context: dict[str, Any] | None = None) -> AgentResult:
        """Execute the agent reasoning loop.

        1. Build prompt from task + context
        2. Call Claude with tools
        3. For each tool_use: check policy → execute or block → feed result back
        4. Repeat until end_turn or max_turns
        5. Log audit trail
        """
        context = context or {}
        start_time = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._build_prompt(task, context)},
        ]
        actions_taken: list[dict[str, Any]] = []
        policy_log: list[dict[str, Any]] = []
        total_tokens = 0
        escalated = False
        turns = 0

        try:
            while turns < self.max_turns:
                response = await self.client.messages.create(
                    model=settings.claude_model,
                    system=self.system_prompt,
                    max_tokens=self.max_tokens_per_turn,
                    tools=self.tools.get_definitions(),
                    messages=messages,
                )

                total_tokens += response.usage.input_tokens + response.usage.output_tokens

                # End turn — Claude is done reasoning
                if response.stop_reason == "end_turn":
                    final_text = self._extract_text(response)
                    confidence = self._extract_confidence(final_text)

                    # Auto-escalate on low confidence
                    if confidence == "low" and not escalated:
                        escalated = True
                        try:
                            await self._slack.send_blocks([
                                {"type": "header", "text": {"type": "plain_text", "text": f":warning: Low Confidence — {self.name}"}},
                                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Agent is not confident in its response.*\n\n{final_text[:500]}"}},
                            ], text=f"Low confidence from {self.name}")
                        except Exception:
                            logger.exception("Failed to send low-confidence escalation")

                    result = AgentResult(
                        success=True,
                        message=final_text,
                        actions_taken=actions_taken,
                        escalated=escalated,
                        tokens_used=total_tokens,
                        confidence=confidence,
                    )
                    await self._log_audit(task, actions_taken, policy_log, result, start_time)
                    return result

                # Tool use — process ALL tool calls in this response
                if response.stop_reason == "tool_use":
                    tool_results = []

                    for block in response.content:
                        if block.type != "tool_use":
                            continue

                        # GUARDRAIL CHECK — intercept before execution
                        decision = await self.policies.check(
                            block.name, block.input, context
                        )
                        policy_log.append({
                            "tool": block.name,
                            "action": decision.action,
                            "policy": decision.policy_name,
                            "reason": decision.reason,
                        })

                        if decision.action == "deny":
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"BLOCKED by {decision.policy_name}: {decision.reason}",
                                "is_error": True,
                            })
                            actions_taken.append({
                                "tool": block.name,
                                "status": "denied",
                                "reason": decision.reason,
                            })

                        elif decision.action == "escalate":
                            escalated = True
                            await self._escalate_to_slack(block, decision, context)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"ESCALATED to human review ({decision.policy_name}): {decision.reason}",
                                "is_error": True,
                            })
                            actions_taken.append({
                                "tool": block.name,
                                "status": "escalated",
                                "reason": decision.reason,
                            })

                        else:  # allow
                            try:
                                result_data = await self.tools.execute(
                                    block.name, block.input
                                )
                                serialized = json.dumps(result_data, default=str)
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": serialized,
                                })
                                actions_taken.append({
                                    "tool": block.name,
                                    "status": "executed",
                                    "input": block.input,
                                })
                            except Exception as exc:
                                logger.exception("Tool %s failed", block.name)
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": f"Error executing {block.name}: {exc}",
                                    "is_error": True,
                                })
                                actions_taken.append({
                                    "tool": block.name,
                                    "status": "error",
                                    "error": str(exc),
                                })

                    # Append assistant response + tool results to conversation
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                    turns += 1
                    continue

                # Unexpected stop reason
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                break

            # Max turns exceeded
            result = AgentResult(
                success=False,
                message=f"Agent {self.name} reached max turns ({self.max_turns})",
                actions_taken=actions_taken,
                escalated=True,
                tokens_used=total_tokens,
            )
            await self._log_audit(task, actions_taken, policy_log, result, start_time)
            return result

        except Exception as exc:
            logger.exception("Agent %s failed", self.name)
            result = AgentResult(
                success=False,
                message=f"Agent error: {exc}",
                actions_taken=actions_taken,
                escalated=True,
                tokens_used=total_tokens,
            )
            await self._log_audit(task, actions_taken, policy_log, result, start_time)
            return result

    async def _log_audit(
        self,
        task: str,
        actions: list[dict],
        policy_decisions: list[dict],
        result: AgentResult,
        start_time: float,
    ) -> None:
        """Log the agent run to the audit table."""
        duration_ms = int((time.monotonic() - start_time) * 1000)
        try:
            audit_id = await self.audit.log(
                agent_name=self.name,
                task=task,
                tool_calls=actions,
                policy_decisions=policy_decisions,
                result="success" if result.success else "failed",
                tokens_used=result.tokens_used,
                duration_ms=duration_ms,
                escalated=result.escalated,
            )
            result.audit_id = audit_id
        except Exception:
            logger.exception("Failed to write audit log for agent %s", self.name)
