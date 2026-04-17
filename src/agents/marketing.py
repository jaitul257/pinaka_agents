"""Marketing Agent — full-funnel strategist for Pinaka Jewellery.

Neutral-framed: the agent reviews live data and the current strategy
(returned by a tool), then reports findings and a recommendation. It does
not carry the strategy in its system prompt — that's a memo, and memos
drift. Call `get_current_strategy` to see current truth.
"""

from __future__ import annotations

from src.agents.base import CONFIDENCE_INSTRUCTIONS, BaseAgent
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.slack import SlackNotifier
from src.finance.calculator import FinanceCalculator
from src.marketing import strategy_config
from src.marketing.ads import AdsTracker


SYSTEM_PROMPT = """You are the Marketing Strategist Agent for Pinaka Jewellery — a \
premium handcrafted diamond tennis bracelet brand (~$5,000 AOV, made-to-order, \
15 business day lead time, DTC on pinakajewellery.com).

## Your job (what a strategist actually does)

Review what's happening, report what you find, and recommend an action only \
when the data supports it. You are not executing a preset playbook; you are \
reasoning about current state against the current strategy.

Before you draw any conclusion:
1. Call `get_current_strategy` so you're working against today's rules, not \
   last month's memo. Budget caps, campaign allocation, seasonal windows, \
   and measurement trust order all live there.
2. Call `get_roas` and any other live-data tools you need.
3. Reconcile observed metrics against the strategy's KPIs. Identify gaps.

## Framing rules (protect against bias)

- Platform ROAS at our volume is directional, not authoritative. The \
  `measurement_trust_order` in the strategy tells you which signals to trust.
- Do NOT assume an anomaly is a problem until you've checked threshold context. \
  A single-day spend spike inside a seasonal window is not a bug.
- If the data is ambiguous, say so plainly and mark confidence LOW. Do not \
  invent a recommendation to look useful.

## When you recommend a change

State:
- What you observed (the specific metric vs the specific KPI).
- What the strategy says to do at that threshold.
- What you recommend, and what it would cost / save.
- The confidence level and what would raise it (what additional data you'd want).

## What to post to Slack

A structured report: live ROAS, trend vs last week, active seasonal window (if \
any), creative fatigue flags (if CTR dropped beyond the threshold in the \
strategy), and any anomalies worth a human look. Do not post if there is \
nothing material to report — silence is allowed.
""" + CONFIDENCE_INSTRUCTIONS


class MarketingAgent(BaseAgent):
    """Full-funnel marketing strategist. Strategy is tool-returned data,
    not a baked-in prompt."""

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
            name="get_current_strategy",
            description=(
                "Return the current marketing strategy as data: campaign "
                "allocation, budget rules, measurement trust order, account "
                "defaults, creative strategy, margin rules, and the active "
                "seasonal window (if any). Call this BEFORE drawing any "
                "conclusion — it's the source of truth for the rules you "
                "should reason against. No inputs."
            ),
            input_schema={"type": "object", "properties": {}},
            func=lambda: strategy_config.snapshot(),
            risk_tier=1,
        )

        self.tools.register(
            name="get_roas",
            description=(
                "Calculate ROAS from daily_stats over the given window. "
                "Returns total_ad_spend, total_revenue, roas (or null if "
                "spend < $1), a recommendation (increase/maintain/decrease/pause), "
                "and the current daily budget cap. "
                "window_days must be between 7 and 90; default 30. "
                "At N=1 the signal is noise — refuse and recommend N>=7. "
                "All dates assumed US/Eastern timezone."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "daily_stats": {
                        "type": "array",
                        "description": "Array of daily_stats records from context",
                        "items": {"type": "object"},
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "Window length in days. Valid range 7-90. Default 30.",
                        "minimum": 7,
                        "maximum": 90,
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
                "Return the active seasonal window today (Valentine's, "
                "Mother's Day, Holiday, etc.) as {name, angle, days_left, "
                "budget_multiplier} — or null if no window is active. "
                "Already bundled inside get_current_strategy, but available "
                "as a standalone if that's all you need. No inputs."
            ),
            input_schema={"type": "object", "properties": {}},
            func=lambda: strategy_config.check_seasonal_window(),
            risk_tier=1,
        )

        self.tools.register(
            name="calculate_profit",
            description=(
                "Calculate net profit for a single order. Returns revenue, "
                "cogs, shopify_fees (2.9% + $0.30), shipping_cost, ad_spend, "
                "net_profit, and margin_pct. Use this to check per-product "
                "margin against the strategy's margin_rules (prioritize > 40%, "
                "flag < 20%, alert if negative)."
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
                "Post a structured marketing report to Slack. Only call this "
                "if there is something material to report — if ROAS is flat, "
                "no seasonal window, no fatigue, no anomalies, skip the post. "
                "Silence is a valid outcome."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Formatted marketing report for Slack",
                    },
                },
                "required": ["message"],
            },
            func=self._post_slack_wrapper,
            risk_tier=1,
        )

    def _get_roas_wrapper(
        self, daily_stats: list, window_days: int | None = None,
    ) -> dict:
        window = window_days if window_days is not None else 30
        if window < 7 or window > 90:
            return {
                "error": "window_days out of range",
                "allowed_range": "7-90",
                "recommendation": "Use default 30 or explicitly pick within range.",
            }
        result = self._ads.calculate_roas(daily_stats, window)
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
            {"type": "header", "text": {"type": "plain_text",
                                        "text": ":chart_with_upwards_trend: Marketing Strategy Report"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
            {"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"_Agent: {self.name} | Strategy loaded via tool, not prompt_"}
            ]},
        ]
        await self._slack_notifier.send_blocks(blocks, text=message[:200])
        return {"posted": True}


# Backwards-compat shims: ugc_brief.py and dashboard/brief.py still import
# SEASONAL_CALENDAR and _check_seasonal_window from here. Leave as re-exports
# until those callers are migrated to strategy_config directly.
SEASONAL_CALENDAR = strategy_config.SEASONAL_CALENDAR


def _check_seasonal_window() -> dict | None:
    return strategy_config.check_seasonal_window()
