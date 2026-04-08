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


class BaseAgent:
    """Core agent with Claude tool_use loop and guardrail interception.

    Subclasses set: name, system_prompt, and call _register_tools() to populate
    the ToolRegistry with domain-specific tools.
    """

    name: str = "base"
    system_prompt: str = "You are a helpful assistant."
    max_turns: int = 15
    max_tokens_per_turn: int = 4096

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

        # Let subclasses register their tools
        self._register_tools()

    def _register_tools(self) -> None:
        """Override in subclasses to register domain-specific tools."""

    def _build_prompt(self, task: str, context: dict[str, Any]) -> str:
        """Build the user prompt from task description and assembled context."""
        parts = [f"## Task\n{task}"]
        if context:
            parts.append("## Context")
            for key, value in context.items():
                if isinstance(value, (dict, list)):
                    parts.append(f"### {key}\n```json\n{json.dumps(value, indent=2, default=str)}\n```")
                else:
                    parts.append(f"### {key}\n{value}")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text content from a Claude response."""
        texts = []
        for block in response.content:
            if hasattr(block, "text"):
                texts.append(block.text)
        return "\n".join(texts) if texts else ""

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
                    result = AgentResult(
                        success=True,
                        message=self._extract_text(response),
                        actions_taken=actions_taken,
                        escalated=escalated,
                        tokens_used=total_tokens,
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
