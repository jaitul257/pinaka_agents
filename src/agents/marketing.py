"""Marketing Agent — manages ROAS-based budget recommendations.

Auto-adjusts within $5 of current budget (Level 2 autonomy).
Escalates budget changes > $5 or new campaign creation.
Pulls profit data from finance module to prioritize high-margin products.
"""

from src.agents.base import CONFIDENCE_INSTRUCTIONS, BaseAgent
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.finance.calculator import FinanceCalculator
from src.marketing.ads import AdsTracker

SYSTEM_PROMPT = """You are the Marketing Agent for Pinaka Jewellery, a premium \
handcrafted diamond tennis bracelet brand.

Your responsibilities:
1. Analyze ad performance using get_roas to check the current return on ad spend.
2. Review daily stats to understand revenue vs spend trends over the past week.
3. Make budget recommendations based on ROAS thresholds:
   - ROAS >= 4.0x: Recommend increasing budget (up to 20% increase)
   - ROAS 2.0x - 4.0x: Maintain current budget
   - ROAS < 2.0x: Recommend decreasing budget (30% reduction)
   - ROAS = 0 (no revenue): Recommend pausing if spend is significant
4. Post your analysis and recommendation to Slack.

BUDGET RULES:
- Daily ad budget cap is $75 across all platforms (Meta + Google).
- You can auto-adjust within $5 of the current budget without escalation.
- Changes > $5 must be escalated to Slack for founder approval.
- Never recommend spending above $150/day (2x cap).
- Always explain your reasoning with actual numbers.

FEEDBACK LOOP:
- Use calculate_profit to understand which products have the best margins.
- High-margin products should get budget priority.
- If a product has negative margin, flag it immediately.
""" + CONFIDENCE_INSTRUCTIONS


class MarketingAgent(BaseAgent):
    """Analyzes ad performance and recommends budget adjustments."""

    name = "marketing"
    system_prompt = SYSTEM_PROMPT
    max_turns = 5  # ROAS calc + recommendation + post — rarely needs more

    def __init__(self):
        self._db = AsyncDatabase()
        self._ads = AdsTracker()
        self._finance = FinanceCalculator()
        self._slack_notifier = SlackNotifier()
        super().__init__()

    def _register_tools(self):

        self.tools.register(
            name="get_roas",
            description=(
                "Get current ROAS (Return on Ad Spend) for the past N days. "
                "Returns total ad spend, total revenue, ROAS ratio, and a budget "
                "recommendation (increase/maintain/decrease/pause)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "daily_stats": {
                        "type": "array",
                        "description": "Array of daily_stats records from the database",
                        "items": {"type": "object"},
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Number of days to calculate over (default 30)",
                    },
                },
                "required": ["daily_stats"],
            },
            func=self._get_roas_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="calculate_profit",
            description=(
                "Calculate net profit for an order. Returns revenue, COGS, fees, "
                "shipping, ad spend, net profit, and margin percentage."
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
            name="post_to_slack",
            description="Post analysis and recommendations to the Slack ops channel.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message text for Slack"},
                },
                "required": ["message"],
            },
            func=self._post_slack_wrapper,
            risk_tier=1,
        )

    def _get_roas_wrapper(self, daily_stats: list, window_days: int | None = None) -> dict:
        result = self._ads.calculate_roas(daily_stats, window_days)
        return {
            "window_days": result.window_days,
            "total_ad_spend": result.total_ad_spend,
            "total_revenue": result.total_revenue,
            "roas": result.roas,
            "recommendation": result.recommendation,
            "recommended_budget": result.recommended_budget,
            "current_budget": result.current_budget,
        }

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
            {"type": "header", "text": {"type": "plain_text", "text": ":chart_with_upwards_trend: Marketing Agent"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Agent: {self.name} | Ad performance & budget_"}]},
        ]
        await self._slack_notifier.send_blocks(blocks, text=message[:200])
        return {"posted": True}
