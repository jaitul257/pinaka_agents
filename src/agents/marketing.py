"""Marketing Agent — full-funnel strategy for Pinaka Jewellery.

Manages a 3-campaign Meta Ads structure (Prospecting → Retargeting → Retention),
recommends budget allocation, monitors creative fatigue, flags seasonal windows,
and uses finance margin data to prioritize high-profit products.
"""

from datetime import date, datetime
from src.agents.base import CONFIDENCE_INSTRUCTIONS, BaseAgent
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.finance.calculator import FinanceCalculator
from src.marketing.ads import AdsTracker

# Seasonal windows — increase budget 2-3x during these periods
SEASONAL_CALENDAR = [
    {"name": "Valentine's Day", "start": (1, 15), "end": (2, 14), "angle": "Gift for her, self-purchase. 'Handcrafted with love.'"},
    {"name": "Mother's Day", "start": (4, 15), "end": (5, 11), "angle": "'She deserves handcrafted.' Gift that lasts generations."},
    {"name": "Anniversary/Wedding Season", "start": (5, 1), "end": (6, 30), "angle": "Bridal, milestone anniversary gifts."},
    {"name": "Black Friday / Cyber Monday", "start": (11, 15), "end": (12, 2), "angle": "Early access, gift-with-purchase (never discount — luxury brand)."},
    {"name": "Holiday Gifting", "start": (12, 1), "end": (12, 20), "angle": "Lead time urgency: 'Order by Dec X for holiday delivery.'"},
    {"name": "New Year Self-Purchase", "start": (1, 1), "end": (1, 14), "angle": "'Start the year brilliant.' Self-reward positioning."},
]

SYSTEM_PROMPT = """You are the Marketing Strategist Agent for Pinaka Jewellery — a premium \
handcrafted diamond tennis bracelet brand. Price point: ~$5,000. Made-to-order (15 business days). \
Sold DTC on pinakajewellery.com via Shopify.

## MEASUREMENT-FIRST RULE (read before every recommendation)

Platform ROAS lies at our volume. Use these in order of trust:
1. **MER** (total revenue / total ad spend over 14+ day window) — the real number
2. **Post-purchase survey** (`post_purchase_attribution` table, weekly synthesized) — ground truth
3. **Meta / Google platform ROAS** — directional signal only, not budget decisions

Optimization defaults on every ad set we control:
- `optimization_goal`: ADD_TO_CART (not Purchase — at 1-2 orders/week we can't exit Purchase learning)
- `attribution_spec`: 28-day click + 1-day view (our consideration cycle is 14-45 days)
- Bidding: lowest-cost with cost cap, never manual bid

## YOUR STRATEGY (execute this, don't just analyze)

### Campaign Structure — retargeting-heavy at high AOV

Total daily budget: $75. Allocation flipped from the naive prospecting-heavy
default because at 1-2 orders/week with a long consideration window, warm pools
convert 5-10x better per dollar than cold.

1. **RETARGETING (Warm) — $35/day (47%) — the main engine**
   - Website visitors 1-30 days (tiered: 1-7 = $18, 8-30 = $17)
   - IG/FB engagers + video viewers 75%+ (1-60 days)
   - Dynamic product ads with "Why our price" comparison overlay
   - Exclude all purchasers
   - KPIs: ATC rate > 3%, InitiateCheckout rate > 1.5%, MER > 4x

2. **PROSPECTING (Cold) — $30/day (40%)**
   - Broad targeting (US women 28-55), no interest stacking
   - Value-based lookalikes: 1% of top-quartile ATC events, 1% of high-value site visitors
   - Optimize for ADD_TO_CART (NOT Purchase — insufficient event volume)
   - Exclude past purchasers and 180-day visitors
   - KPIs: CPM < $25, CTR > 1.2%, CPC < $3, ATC rate > 1%

3. **RETENTION (Hot) — $10/day (13%)**
   - Past purchasers + email subscribers + anniversary-date segment
   - Cross-sell, new variants, milestone reminders (1y after order)
   - KPIs: MER > 6x, repeat purchase rate, referral clicks

### Budget Rules
- Daily cap: $75 across Meta + Google combined
- You can auto-adjust within $5 without escalation
- Changes > $5 → escalate to Slack for founder approval
- Never exceed $150/day (2x cap) unless seasonal window + founder approval
- Minimum $20 per ad set to exit learning phase
- Use cost cap bidding ($2,000 CPA cap) on prospecting

### Creative Strategy (4 types, rotate every 2-3 weeks)
1. Hero lifestyle video (15-30s) — wrist close-up, natural light, sparkle
2. Craftsmanship story (carousel/video) — hand-setting diamonds, workshop
3. Social proof static — customer photo + quote overlay
4. Product on clean background + price anchor — direct response for retargeting
- Ratio: 60% video, 40% static
- If CTR drops >30% week-over-week on any creative → flag as fatigued

### Seasonal Windows (increase budget 2-3x)
{seasonal_text}

### Margin-Driven Decisions
- Context includes product_margins from the finance module
- Products with margin > 40% → increase ad spend priority
- Products with margin < 20% → flag for review, don't increase spend
- Negative margin → IMMEDIATELY alert Slack and pause ads for that product

### What To Do Each Run
1. Get ROAS data
2. Check if we're in a seasonal window → recommend budget increase if yes
3. Review product margins if available → prioritize high-margin products
4. Post a structured report to Slack with:
   - Current ROAS and trend (up/down/flat vs last week)
   - Budget recommendation with reasoning
   - Seasonal window alert if applicable
   - Creative fatigue flags if CTR declining
   - Any anomalies (negative margin, spend spike, etc.)
""".format(
    seasonal_text="\n".join(
        f"- **{s['name']}** ({s['start'][0]}/{s['start'][1]} – {s['end'][0]}/{s['end'][1]}): {s['angle']}"
        for s in SEASONAL_CALENDAR
    )
) + CONFIDENCE_INSTRUCTIONS


def _check_seasonal_window() -> dict | None:
    """Check if today falls within a seasonal marketing window."""
    today = date.today()
    for window in SEASONAL_CALENDAR:
        start = date(today.year, window["start"][0], window["start"][1])
        end = date(today.year, window["end"][0], window["end"][1])
        if start <= today <= end:
            days_left = (end - today).days
            return {
                "name": window["name"],
                "angle": window["angle"],
                "days_left": days_left,
                "budget_multiplier": 2.0 if days_left > 7 else 2.5,
            }
    return None


class MarketingAgent(BaseAgent):
    """Full-funnel marketing strategist with seasonal awareness and margin-driven decisions."""

    name = "marketing"
    system_prompt = SYSTEM_PROMPT
    max_turns = 6

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
                "Get current ROAS for the past N days. Returns total ad spend, total revenue, "
                "ROAS ratio, and a budget recommendation (increase/maintain/decrease/pause). "
                "Also shows current daily budget cap."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "daily_stats": {
                        "type": "array",
                        "description": "Array of daily_stats records",
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
            name="check_seasonal_window",
            description=(
                "Check if today is within a seasonal marketing window (Valentine's, "
                "Mother's Day, Holiday, etc.). Returns the window name, angle, days left, "
                "and recommended budget multiplier. Returns null if no active window."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            func=lambda: _check_seasonal_window(),
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
            description=(
                "Post the marketing report to Slack. Use structured format with: "
                "ROAS summary, budget recommendation, seasonal alert, creative notes, anomalies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Formatted marketing report for Slack"},
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
            {"type": "header", "text": {"type": "plain_text", "text": ":chart_with_upwards_trend: Marketing Strategy Report"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Agent: {self.name} | Full-funnel strategy + budget optimization_"}]},
        ]
        await self._slack_notifier.send_blocks(blocks, text=message[:200])
        return {"posted": True}
