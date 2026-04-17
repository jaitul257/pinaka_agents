"""Order Operations Agent — handles new order lifecycle autonomously.

Flow: new order → fraud check → insurance validation → ShipStation creation
→ crafting update scheduling → order confirmation email.

If fraud is flagged or order exceeds insurance cap, escalates to Slack
instead of proceeding. All actions are logged to the audit trail.
"""

from src.agents.base import CONFIDENCE_INSTRUCTIONS, BaseAgent
from src.agents.context import ContextAssembler
from src.agents.guardrails import PolicyEngine
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.email import EmailSender
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.finance.calculator import FinanceCalculator
from src.shipping.processor import ShippingProcessor

SYSTEM_PROMPT = """You are the Order Operations Agent for Pinaka Jewellery, a premium \
handcrafted diamond tennis bracelet brand (~$5,000 AOV, made-to-order, 15 \
business day lead time).

## Your job

Process a new order from Shopify through to ShipStation handoff, verifying \
fraud and insurance conditions on the way. Report a concise summary at the \
end. Escalate when the data warrants it, not by default.

## Hard rules (non-negotiable, safety-critical)

- Every order gets `check_fraud_risk`. No exceptions. Skipping this is a bug.
- If `requires_video_verification` is true (orders above $5,000), stop the \
  sequence and escalate to Slack. Do not create the ShipStation order.
- If a fulfillment tool (`create_shipstation_order`) returns an error, do \
  NOT retry silently — report the failure and escalate. A duplicate \
  ShipStation order costs real money.

## Suggested sequence (the 99% path)

1. `lookup_order` + `lookup_customer` to ground yourself.
2. `check_fraud_risk` — act on the result.
3. `validate_insurance` — note the gap but continue; insurance shortfall \
   is a flag, not a stop.
4. `create_shipstation_order` if no fraud stop condition was hit.
5. `calculate_profit` for the Slack summary.
6. `post_to_slack` with a structured summary (fraud status, insurance \
   status, profit, ShipStation ID).

Deviations from this sequence are fine when the data demands it — e.g. a \
repeat customer with clean history doesn't need a second lookup_customer. \
Reason out loud when you deviate.

## Framing

- Errors from non-fulfillment tools (`lookup_*`, `calculate_profit`) do \
  not automatically mean escalation. Investigate first.
- Made-to-order shipping is 15 business days — don't promise faster.
""" + CONFIDENCE_INSTRUCTIONS


class OrderOpsAgent(BaseAgent):
    """Processes new orders: fraud → insurance → ShipStation → confirmation."""

    name = "order_ops"
    system_prompt = SYSTEM_PROMPT
    max_turns = 10  # Order processing is deterministic, shouldn't need many turns

    def __init__(self):
        self._db = AsyncDatabase()
        self._shipping = ShippingProcessor()
        self._email = EmailSender()
        self._finance = FinanceCalculator()
        self._slack_notifier = SlackNotifier()
        super().__init__()

    def _register_tools(self):
        """Register order-related tools."""

        self.tools.register(
            name="lookup_order",
            description=(
                "Read-only. Fetch a single Shopify order row from Supabase by its "
                "numeric Shopify order ID. Returns the full order dict (total in USD, "
                "line_items, status, shipping_address, buyer_email, created_at, "
                "shipstation_order_id if already fulfilled, tracking_number if shipped). "
                "Returns null if the order isn't in our DB (webhook may be delayed — "
                "wait and retry, don't escalate)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "Numeric Shopify order ID (not the '#1234' display number)",
                    },
                },
                "required": ["order_id"],
            },
            func=self._lookup_order_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="lookup_customer",
            description=(
                "Read-only. Fetch a customer row from Supabase by email (case-insensitive). "
                "Returns {id, name, order_count, lifetime_value (USD), lifecycle_stage, "
                "last_order_at, accepts_marketing} or null if unknown. Use to distinguish "
                "first-time buyers from repeat customers in your summary."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Customer email address"},
                },
                "required": ["email"],
            },
            func=self._db.get_customer_by_email,
            risk_tier=1,
        )

        self.tools.register(
            name="check_fraud_risk",
            description=(
                "Read-only. Score an order against fraud heuristics. Returns "
                "{is_flagged: bool, reasons: list[str], requires_video_verification: "
                "bool, insurance_gap: float}. Thresholds: total > $5,000 sets "
                "requires_video_verification; email+IP velocity > 2 orders/24h sets "
                "is_flagged; mismatched shipping/billing country sets is_flagged. "
                "insurance_gap is max(0, total - $2,500)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order": {
                        "type": "object",
                        "description": "Order dict with at minimum: total (number), buyer_email (string), shipping_address, billing_address",
                    },
                },
                "required": ["order"],
            },
            func=self._check_fraud_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="validate_insurance",
            description=(
                "Read-only. Compare order total against the $2,500 carrier insurance "
                "cap. Returns {covered: bool, insured_value (USD), gap (USD)}. A gap > 0 "
                "indicates need for supplemental Shipsurance coverage — flag in the "
                "summary but do NOT stop the fulfillment sequence on gap alone."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_total": {
                        "type": "number",
                        "description": "Order total in USD (positive number)",
                    },
                },
                "required": ["order_total"],
            },
            func=self._shipping.validate_insurance,
            risk_tier=1,
        )

        self.tools.register(
            name="create_shipstation_order",
            description=(
                "SIDE EFFECT — creates a new order in ShipStation. NOT idempotent: "
                "calling twice creates a duplicate order and real duplicate shipping "
                "labels. Only call after check_fraud_risk passes (or fraud stop "
                "condition is manually waived). On HTTP error, do NOT retry — escalate "
                "to Slack and let a human decide. Returns ShipStation's response "
                "object with orderId on success."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_data": {
                        "type": "object",
                        "description": "Full order payload: shipping_address, billing_address, line_items, total (USD), buyer_email, shopify_order_id",
                    },
                },
                "required": ["order_data"],
            },
            func=self._create_shipstation_wrapper,
            risk_tier=2,
        )

        self.tools.register(
            name="calculate_profit",
            description=(
                "Read-only. Compute P&L for one order. Shopify fee is 2.9% + $0.30 "
                "(applied inside). Returns {revenue, cogs, shopify_fees, shipping_cost, "
                "ad_spend, net_profit, margin_pct} all in USD. If cogs or ad_spend are "
                "missing from the order, they're treated as zero — margin_pct will be "
                "optimistic in that case. Flag if margin_pct < 20."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order": {
                        "type": "object",
                        "description": "Order dict with total (required); cogs, shipping_cost, ad_spend optional (default 0)",
                    },
                },
                "required": ["order"],
            },
            func=self._calculate_profit_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="post_to_slack",
            description=(
                "SIDE EFFECT — posts one message to the founder's Slack channel. "
                "Use for: order summaries after successful fulfillment, escalations "
                "(fraud stop conditions, tool errors), or insurance-gap flags. One "
                "message per run — do not spam. Message is truncated at 2900 chars."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Text or simple markdown, max ~2900 chars",
                    },
                },
                "required": ["message"],
            },
            func=self._post_slack_wrapper,
            risk_tier=1,
        )

    # ── Tool wrappers (adapt existing function signatures) ──

    async def _lookup_order_wrapper(self, order_id: int) -> dict | None:
        return await self._db.get_order_by_shopify_id(order_id)

    async def _check_fraud_wrapper(self, order: dict) -> dict:
        result = await self._shipping.check_fraud(order)
        return {
            "is_flagged": result.is_flagged,
            "reasons": result.reasons,
            "requires_video_verification": result.requires_video_verification,
            "insurance_gap": result.insurance_gap,
        }

    async def _create_shipstation_wrapper(self, order_data: dict) -> dict:
        return await self._shipping.create_shipstation_order(order_data)

    def _calculate_profit_wrapper(self, order: dict) -> dict:
        result = self._finance.calculate_order_profit(order)
        return {
            "revenue": result.revenue,
            "cogs": result.cogs,
            "shopify_fees": result.shopify_fees,
            "shipping_cost": result.shipping_cost,
            "ad_spend": result.ad_spend,
            "net_profit": result.net_profit,
            "margin_pct": result.margin_pct,
        }

    async def _post_slack_wrapper(self, message: str) -> dict:
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": ":package: Order Ops Agent"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Agent: {self.name} | Automated processing_"}]},
        ]
        await self._slack_notifier.send_blocks(blocks, text=message[:200])
        return {"posted": True}
