"""PolicyEngine — three-layer guardrail system for agent tool calls.

Layer 1 (Input): Token budget, context validation — checked before agent runs.
Layer 2 (Execution): Per-tool-call policies — checked before each tool executes.
Layer 3 (Output): Content filtering — checked on agent's final response.

Each Policy returns None (no opinion) or a PolicyDecision (allow/deny/escalate).
First non-None decision wins. If all return None, the default is ALLOW.
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from src.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""

    action: Literal["allow", "deny", "escalate"]
    reason: str
    policy_name: str


class Policy(ABC):
    """Base class for all guardrail policies."""

    name: str = "unnamed_policy"

    @abstractmethod
    async def evaluate(
        self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]
    ) -> PolicyDecision | None:
        """Evaluate a tool call. Return None to abstain, or a PolicyDecision."""
        ...


class PolicyEngine:
    """Evaluates tool calls against all registered policies.

    First non-None decision wins. Default is ALLOW if no policy objects.
    """

    def __init__(self, policies: list[Policy] | None = None):
        self.policies = policies or self._default_policies()

    @staticmethod
    def _default_policies() -> list[Policy]:
        """Standard policies based on CLAUDE.md business rules."""
        return [
            HumanRequiredPolicy(),
            FraudEscalationPolicy(),
            HighValueOrderPolicy(),
            SpendingLimitPolicy(),
            EmailRateLimitPolicy(),
            ContentFilterPolicy(),
            TokenBudgetPolicy(),
        ]

    async def check(
        self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]
    ) -> PolicyDecision:
        """Run all policies against a tool call. First non-None decision wins."""
        for policy in self.policies:
            try:
                decision = await policy.evaluate(tool_name, tool_input, context)
                if decision is not None:
                    logger.info(
                        "Policy %s: %s for tool %s — %s",
                        policy.name, decision.action, tool_name, decision.reason,
                    )
                    return decision
            except Exception:
                logger.exception("Policy %s raised an error", policy.name)

        # Default: allow
        return PolicyDecision(action="allow", reason="No policy objected", policy_name="default")

    async def validate_output(
        self, agent_response: str, context: dict[str, Any]
    ) -> PolicyDecision:
        """Layer 3: Validate agent's final text output."""
        # Check for banned words in outbound content
        banned = _get_banned_words()
        response_lower = agent_response.lower()
        found = [w for w in banned if w in response_lower]
        if found:
            return PolicyDecision(
                action="deny",
                reason=f"Output contains banned words: {', '.join(found[:5])}",
                policy_name="output_filter",
            )
        return PolicyDecision(action="allow", reason="Output passed filters", policy_name="output_filter")


# ── Concrete Policies ──


class HumanRequiredPolicy(Policy):
    """Always escalate: refunds, complaints, pricing changes. Non-negotiable."""

    name = "human_required"

    # Tools that always require human approval
    ESCALATE_TOOLS = {
        "process_refund",
        "update_pricing",
        "cancel_order",
        "create_discount",
    }

    # Message categories that always escalate
    ESCALATE_CATEGORIES = {"complaint", "refund_request", "return_request"}

    async def evaluate(self, tool_name, tool_input, context):
        if tool_name in self.ESCALATE_TOOLS:
            return PolicyDecision(
                action="escalate",
                reason=f"Tool '{tool_name}' always requires human approval",
                policy_name=self.name,
            )

        # If sending an email and the message category is complaint/refund/return
        if tool_name in ("send_email", "draft_customer_reply"):
            category = tool_input.get("category") or context.get("message_category", "")
            if category in self.ESCALATE_CATEGORIES:
                return PolicyDecision(
                    action="escalate",
                    reason=f"Category '{category}' requires human review before responding",
                    policy_name=self.name,
                )

        return None


class FraudEscalationPolicy(Policy):
    """Escalate if fraud check flags the order."""

    name = "fraud_escalation"

    async def evaluate(self, tool_name, tool_input, context):
        # Check if context already has fraud flags
        fraud = context.get("fraud_check")
        if fraud and fraud.get("is_flagged"):
            # Block any action tools when fraud is flagged
            action_tools = {
                "create_shipstation_order", "send_email", "send_crafting_update",
                "send_delivery_confirmation", "send_order_confirmation",
            }
            if tool_name in action_tools:
                reasons = fraud.get("reasons", ["fraud flagged"])
                return PolicyDecision(
                    action="escalate",
                    reason=f"Order flagged for fraud: {', '.join(reasons)}",
                    policy_name=self.name,
                )
        return None


class HighValueOrderPolicy(Policy):
    """Escalate actions on orders above the insurance threshold ($2,500)."""

    name = "high_value_order"

    async def evaluate(self, tool_name, tool_input, context):
        # Only applies to order-action tools
        action_tools = {"create_shipstation_order", "send_email", "send_order_confirmation"}
        if tool_name not in action_tools:
            return None

        order_total = context.get("order_total") or context.get("order", {}).get("total")
        if order_total and float(order_total) > settings.carrier_insurance_cap:
            return PolicyDecision(
                action="escalate",
                reason=f"Order value ${float(order_total):,.2f} exceeds insurance cap ${settings.carrier_insurance_cap:,.2f}",
                policy_name=self.name,
            )
        return None


class SpendingLimitPolicy(Policy):
    """Block ad spend actions that would exceed daily budget cap."""

    name = "spending_limit"

    async def evaluate(self, tool_name, tool_input, context):
        budget_tools = {"adjust_ad_budget", "create_campaign", "set_daily_budget"}
        if tool_name not in budget_tools:
            return None

        proposed = float(tool_input.get("budget", 0) or tool_input.get("amount", 0))
        if proposed > settings.max_daily_ad_budget:
            return PolicyDecision(
                action="deny",
                reason=f"Proposed budget ${proposed:.2f} exceeds max daily cap ${settings.max_daily_ad_budget:.2f}",
                policy_name=self.name,
            )
        return None


class EmailRateLimitPolicy(Policy):
    """Enforce email frequency limits: max 2 cart recovery/week, 180-day reorder cooldown."""

    name = "email_rate_limit"

    async def evaluate(self, tool_name, tool_input, context):
        if tool_name == "send_cart_recovery":
            recent_count = context.get("cart_recovery_count_this_week", 0)
            if recent_count >= settings.max_cart_recovery_emails_per_week:
                return PolicyDecision(
                    action="deny",
                    reason=f"Customer already received {recent_count} cart recovery emails this week (max {settings.max_cart_recovery_emails_per_week})",
                    policy_name=self.name,
                )

        if tool_name == "send_reorder_reminder":
            last_sent = context.get("last_reorder_email_at")
            if last_sent:
                try:
                    last_dt = datetime.fromisoformat(last_sent)
                    cooldown = timedelta(days=settings.reorder_cooldown_days)
                    if datetime.utcnow() - last_dt < cooldown:
                        days_left = (cooldown - (datetime.utcnow() - last_dt)).days
                        return PolicyDecision(
                            action="deny",
                            reason=f"Reorder cooldown: {days_left} days remaining (180-day minimum)",
                            policy_name=self.name,
                        )
                except (ValueError, TypeError):
                    pass

        return None


class ContentFilterPolicy(Policy):
    """Scan outbound text for banned words/phrases from brand guidelines."""

    name = "content_filter"

    async def evaluate(self, tool_name, tool_input, context):
        # Only check tools that produce customer-facing text
        text_tools = {"send_email", "draft_customer_reply", "send_cart_recovery", "send_reorder_reminder"}
        if tool_name not in text_tools:
            return None

        # Check email_body or text fields for banned content
        text_fields = ["email_body", "body", "text", "subject"]
        combined_text = " ".join(
            str(tool_input.get(f, "")) for f in text_fields
        ).lower()

        if not combined_text.strip():
            return None

        banned = _get_banned_words()
        found = [w for w in banned if w in combined_text]
        if found:
            return PolicyDecision(
                action="deny",
                reason=f"Content contains banned words: {', '.join(found[:5])}",
                policy_name=self.name,
            )
        return None


class TokenBudgetPolicy(Policy):
    """Deny if daily Claude token usage exceeds the configured budget."""

    name = "token_budget"

    async def evaluate(self, tool_name, tool_input, context):
        # This is checked before the agent run, not per-tool-call.
        # Context should include today's token usage from audit log.
        tokens_used_today = context.get("tokens_used_today", 0)
        if tokens_used_today > settings.daily_token_budget:
            return PolicyDecision(
                action="deny",
                reason=f"Daily token budget exceeded: {tokens_used_today:,} / {settings.daily_token_budget:,}",
                policy_name=self.name,
            )
        return None


# ── Helpers ──


def _get_banned_words() -> list[str]:
    """Get list of banned words/phrases for content filtering."""
    try:
        from src.marketing.brand_dna import BrandDNA
        dna = BrandDNA()
        return [w.lower() for w in (dna.banned_words or [])]
    except Exception:
        # Fallback list if brand_dna not available
        return [
            "cheap", "discount", "bargain", "knockoff", "fake",
            "wholesale", "bulk pricing", "mass produced",
        ]
