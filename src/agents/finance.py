"""Finance Agent — generates daily/weekly reports and flags anomalies.

Autonomously produces financial summaries. Flags negative margins,
unusual order velocity, and ad spend spikes for human review.
"""

from src.agents.base import CONFIDENCE_INSTRUCTIONS, BaseAgent
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.finance.calculator import FinanceCalculator

SYSTEM_PROMPT = """You are the Finance Agent for Pinaka Jewellery, a premium \
handcrafted diamond tennis bracelet brand.

Your responsibilities:
1. Analyze daily stats and order data to produce financial summaries.
2. Calculate per-order profit using calculate_profit.
3. Flag anomalies that need attention:
   - Negative net profit on any order
   - Daily ad spend > $75 (above cap)
   - Order velocity changes (sudden spikes or drops)
   - Revenue drop > 30% week-over-week
4. Post your analysis to Slack with clear numbers.

FORMAT:
- Lead with the key metric (revenue, profit, ROAS).
- Use actual dollar amounts, not percentages alone.
- Compare to previous period when possible.
- End with any anomalies or action items.

RULES:
- Never recommend pricing changes — escalate to founder.
- Never disclose financial details outside Slack.
- Round all dollar amounts to 2 decimal places.
""" + CONFIDENCE_INSTRUCTIONS


class FinanceAgent(BaseAgent):
    """Generates financial reports and flags anomalies."""

    name = "finance"
    system_prompt = SYSTEM_PROMPT
    max_turns = 5  # Calculate + summarize + post

    def __init__(self):
        self._db = AsyncDatabase()
        self._finance = FinanceCalculator()
        self._slack_notifier = SlackNotifier()
        super().__init__()

    def _register_tools(self):

        self.tools.register(
            name="calculate_profit",
            description=(
                "Calculate net profit for an order. Returns revenue, COGS, Shopify fees, "
                "shipping cost, ad spend, net profit, and margin percentage."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order": {"type": "object", "description": "Order dict with total, cogs, shipping_cost, ad_spend"},
                },
                "required": ["order"],
            },
            func=self._calculate_profit_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="lookup_order",
            description="Look up order details by Shopify order ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "Shopify order ID"},
                },
                "required": ["order_id"],
            },
            func=self._lookup_order_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="post_to_slack",
            description="Post financial analysis and reports to the Slack ops channel.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Report text for Slack"},
                },
                "required": ["message"],
            },
            func=self._post_slack_wrapper,
            risk_tier=1,
        )

    async def _lookup_order_wrapper(self, order_id: int) -> dict | None:
        return await self._db.get_order_by_shopify_id(order_id)

    def _calculate_profit_wrapper(self, order: dict) -> dict:
        result = self._finance.calculate_order_profit(order)
        return {
            "revenue": result.revenue, "cogs": result.cogs,
            "shopify_fees": result.shopify_fees, "shipping_cost": result.shipping_cost,
            "ad_spend": result.ad_spend, "net_profit": result.net_profit,
            "margin_pct": result.margin_pct,
        }

    async def _post_slack_wrapper(self, message: str) -> dict:
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": ":moneybag: Finance Agent"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Agent: {self.name} | Financial reporting_"}]},
        ]
        await self._slack_notifier.send_blocks(blocks, text=message[:200])
        return {"posted": True}
