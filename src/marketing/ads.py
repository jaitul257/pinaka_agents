"""Ad performance tracker and budget recommendation engine.

Tracks Google Shopping + Meta ad spend via daily_stats records,
calculates rolling ROAS, and recommends daily budget adjustments.

Phase 5 adds automated ad spend pull from Meta + Google APIs.
ROAS uses blended revenue (total store revenue / total ad spend).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.core.database import Database
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


@dataclass
class ROASResult:
    """Rolling ROAS calculation result."""

    window_days: int
    total_ad_spend: float
    total_revenue: float
    roas: float
    recommendation: str
    recommended_budget: float
    current_budget: float


class AdsTracker:
    """Track ad performance and recommend budget adjustments."""

    def __init__(self):
        self._db = Database()
        self._slack = SlackNotifier()

    def calculate_roas(
        self, daily_stats: list[dict[str, Any]], window_days: int | None = None
    ) -> ROASResult:
        """Calculate ROAS from daily_stats records over a rolling window."""
        window = window_days or settings.roas_window_days

        total_spend = sum(
            float(s.get("ad_spend_google", 0)) + float(s.get("ad_spend_meta", 0))
            for s in daily_stats
        )
        total_revenue = sum(float(s.get("revenue", 0)) for s in daily_stats)

        roas = total_revenue / total_spend if total_spend > 0 else 0.0

        current_budget = settings.max_daily_ad_budget
        recommendation, new_budget = self._budget_recommendation(roas, current_budget)

        return ROASResult(
            window_days=window,
            total_ad_spend=round(total_spend, 2),
            total_revenue=round(total_revenue, 2),
            roas=round(roas, 2),
            recommendation=recommendation,
            recommended_budget=round(new_budget, 2),
            current_budget=current_budget,
        )

    def _budget_recommendation(
        self, roas: float, current_budget: float
    ) -> tuple[str, float]:
        """Determine budget action based on ROAS thresholds."""
        if roas >= settings.roas_increase_threshold:
            new_budget = min(current_budget * 1.2, settings.max_daily_ad_budget * 2)
            return "increase", new_budget
        elif roas >= settings.roas_maintain_min:
            return "maintain", current_budget
        elif roas > 0:
            new_budget = max(current_budget * 0.7, 5.0)
            return "decrease", new_budget
        else:
            return "pause", 0.0

    async def run_weekly_roas_report(self) -> ROASResult:
        """Pull stats from DB, calculate ROAS, send Slack summary."""
        tz = ZoneInfo(settings.business_timezone)
        end = datetime.now(tz).date()
        start = end - timedelta(days=settings.roas_window_days)

        daily_stats = self._db.get_stats_range(start, end)
        result = self.calculate_roas(daily_stats, settings.roas_window_days)

        await self._send_roas_slack(result)
        logger.info(
            "Weekly ROAS report: %.2fx over %d days (spend=$%.2f, rev=$%.2f)",
            result.roas,
            result.window_days,
            result.total_ad_spend,
            result.total_revenue,
        )
        return result

    async def _send_roas_slack(self, result: ROASResult) -> None:
        """Send ROAS summary to Slack with budget recommendation."""
        emoji = {
            "increase": ":chart_with_upwards_trend:",
            "maintain": ":bar_chart:",
            "decrease": ":chart_with_downwards_trend:",
            "pause": ":double_vertical_bar:",
        }.get(result.recommendation, ":bar_chart:")

        action_text = {
            "increase": f"Increase budget to ${result.recommended_budget:.2f}/day",
            "maintain": f"Keep budget at ${result.current_budget:.2f}/day",
            "decrease": f"Reduce budget to ${result.recommended_budget:.2f}/day",
            "pause": "Pause ads — no return on spend",
        }.get(result.recommendation, "Review manually")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} WEEKLY ADS REPORT"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*ROAS:* {result.roas}x"},
                    {"type": "mrkdwn", "text": f"*Window:* {result.window_days} days"},
                    {"type": "mrkdwn", "text": f"*Ad Spend:* ${result.total_ad_spend:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Revenue:* ${result.total_revenue:,.2f}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Recommendation:* {action_text}"},
            },
        ]

        if result.recommendation in ("increase", "decrease"):
            blocks.append({
                "type": "actions",
                "block_id": f"ads_budget_{date.today().isoformat()}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Apply Change"},
                        "style": "primary",
                        "action_id": "apply_budget_change",
                        "value": str(result.recommended_budget),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": "dismiss_budget",
                        "value": "dismiss",
                    },
                ],
            })

        await self._slack.send_blocks(blocks, text=f"Weekly Ads: ROAS {result.roas}x")
