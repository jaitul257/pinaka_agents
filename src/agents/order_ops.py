"""Order Operations Agent — handles new order lifecycle autonomously.

Flow: new order → fraud check → insurance validation → ShipStation creation
→ crafting update scheduling → order confirmation email.

If fraud is flagged or order exceeds insurance cap, escalates to Slack
instead of proceeding. All actions are logged to the audit trail.
"""

from src.agents.base import BaseAgent
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
handcrafted diamond tennis bracelet brand.

When a new order arrives, follow this sequence:
1. Look up the order and customer details using lookup_order and lookup_customer.
2. Run check_fraud_risk — if flagged, STOP and post_to_slack to escalate immediately. Do not proceed with fulfillment.
3. Check validate_insurance — note if there is a coverage gap.
4. Create the ShipStation fulfillment order using create_shipstation_order.
5. Calculate the order profit using calculate_profit.
6. Post a summary to Slack with order details, fraud status, insurance status, and profit.

IMPORTANT RULES:
- Never skip the fraud check. Every single order gets checked.
- If fraud is flagged with requires_video_verification=true, you MUST escalate. Do not proceed with fulfillment.
- All bracelets are made-to-order (15 business days). Set expectations accordingly.
- If any tool returns an error, explain the issue and escalate to Slack for human review.
- Be concise in your reasoning. State what you're doing and why at each step.
"""


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
                "Look up order details by Shopify order ID. Returns order total, "
                "line items, status, shipping address, customer email, and all stored "
                "fields. Use this first when handling any order-related task."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "The Shopify order ID (numeric)",
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
                "Get customer profile by email address. Returns name, order history count, "
                "lifetime value, lifecycle stage, last order date, accepts_marketing flag. "
                "Check this before sending any customer communication."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Customer's email address",
                    },
                },
                "required": ["email"],
            },
            func=self._db.get_customer_by_email,
            risk_tier=1,
        )

        self.tools.register(
            name="check_fraud_risk",
            description=(
                "Evaluate fraud risk for an order. Pass the full order dict. Returns "
                "is_flagged (bool), reasons (list of strings), requires_video_verification "
                "(bool for orders > $5,000), and insurance_gap (float). ALWAYS run this "
                "on every new order before proceeding with fulfillment."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order": {
                        "type": "object",
                        "description": "The order dict with total, buyer_email, and other fields",
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
                "Check if carrier insurance covers the order value. Returns covered (bool), "
                "insured_value, and gap amount. Carrier cap is $2,500. Orders above that "
                "need supplemental Shipsurance coverage."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_total": {
                        "type": "number",
                        "description": "The order total in dollars",
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
                "Create an order in ShipStation for fulfillment. Pass the full order data "
                "including shipping_address, billing_address, and line_items. Returns the "
                "ShipStation order response with orderId on success."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_data": {
                        "type": "object",
                        "description": "Order data with shipping_address, billing_address, line_items, total, buyer_email",
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
                "Calculate net profit for an order. Pass the order dict with total, cogs, "
                "shipping_cost, ad_spend fields. Returns revenue, COGS, Shopify fees, "
                "shipping cost, ad cost, net profit, and margin percentage."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order": {
                        "type": "object",
                        "description": "Order dict with total, cogs, shipping_cost, ad_spend",
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
                "Post a message to the Slack ops channel. Use for escalation, status "
                "updates, or requesting human approval. Pass a text message."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message text to post to Slack",
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
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":robot_face: *Order Ops Agent*\n{message}"},
            }
        ]
        await self._slack_notifier.send_blocks(blocks, text=message)
        return {"posted": True}
