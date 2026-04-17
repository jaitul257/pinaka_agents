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

from src.core.database import AsyncDatabase
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
    mer: float = 0.0  # Marketing Efficiency Ratio — see calculate_mer docstring


@dataclass
class MERResult:
    """Marketing Efficiency Ratio — the 'honest' ratio at low event volume.

    MER = (total store revenue) / (total marketing spend). Unlike ROAS, MER
    isn't confused by platform attribution noise, iOS signal loss, or the
    7/28-day click-window gap. At 1-2 orders/week with a 14-45-day
    consideration cycle, MER is the only number that doesn't lie.
    """

    window_days: int
    total_revenue: float
    total_ad_spend: float
    mer: float


def _aggregate_creatives(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group per-ad daily metrics into per-ad-name weekly summaries.

    Returns list sorted by spend desc. Name is taken from `ad_name` or
    `creative_name` falling back to ad_id. Only includes ads with ≥1
    impression this week.
    """
    from collections import defaultdict

    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "name": "", "impressions": 0, "clicks": 0,
        "spend": 0.0, "purchases": 0, "purchase_value": 0.0,
    })
    for r in rows:
        key = r.get("meta_ad_id") or ""
        if not key:
            continue
        item = agg[key]
        # Keep first non-empty name seen (they shouldn't vary for a single ad_id)
        if not item["name"]:
            item["name"] = r.get("ad_name") or r.get("creative_name") or key
        item["impressions"] += int(r.get("impressions") or 0)
        item["clicks"] += int(r.get("clicks") or 0)
        item["spend"] += float(r.get("spend") or 0)
        item["purchases"] += int(r.get("purchase_count") or 0)
        item["purchase_value"] += float(r.get("purchase_value") or 0)

    result: list[dict[str, Any]] = []
    for item in agg.values():
        if item["impressions"] == 0:
            continue
        item["ctr"] = round(item["clicks"] / item["impressions"] * 100, 2)
        item["spend"] = round(item["spend"], 2)
        item["purchase_value"] = round(item["purchase_value"], 2)
        result.append(item)

    result.sort(key=lambda x: x["spend"], reverse=True)
    return result


class AdsTracker:
    """Track ad performance and recommend budget adjustments."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._slack = SlackNotifier()

    def calculate_roas(
        self, daily_stats: list[dict[str, Any]], window_days: int | None = None
    ) -> ROASResult:
        """Calculate ROAS + MER from daily_stats records over a rolling window."""
        window = window_days or settings.roas_window_days

        total_spend = sum(
            float(s.get("ad_spend_google", 0)) + float(s.get("ad_spend_meta", 0))
            for s in daily_stats
        )
        total_revenue = sum(float(s.get("revenue", 0)) for s in daily_stats)

        roas = total_revenue / total_spend if total_spend > 0 else 0.0
        # MER = same calc at our stage (all revenue / all marketing spend).
        # Separate field so we can diverge later (e.g. include email revenue,
        # subtract non-marketing cost, etc.) without touching callers.
        mer = roas

        current_budget = settings.max_daily_ad_budget
        recommendation, new_budget = self._budget_recommendation(roas, current_budget)

        return ROASResult(
            window_days=window,
            total_ad_spend=round(total_spend, 2),
            total_revenue=round(total_revenue, 2),
            roas=round(roas, 2),
            mer=round(mer, 2),
            recommendation=recommendation,
            recommended_budget=round(new_budget, 2),
            current_budget=current_budget,
        )

    def calculate_mer(
        self, daily_stats: list[dict[str, Any]], window_days: int | None = None
    ) -> MERResult:
        """Marketing Efficiency Ratio: total revenue / total ad spend.

        Prefer over platform ROAS for budget decisions at low event volume.
        A healthy DTC MER is 3x-5x at our AOV. Below 2x = something's leaking.
        """
        window = window_days or settings.roas_window_days
        total_spend = sum(
            float(s.get("ad_spend_google", 0)) + float(s.get("ad_spend_meta", 0))
            for s in daily_stats
        )
        total_revenue = sum(float(s.get("revenue", 0)) for s in daily_stats)
        mer = total_revenue / total_spend if total_spend > 0 else 0.0
        return MERResult(
            window_days=window,
            total_revenue=round(total_revenue, 2),
            total_ad_spend=round(total_spend, 2),
            mer=round(mer, 2),
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
        """Pull stats from DB, calculate ROAS, send Slack summary with per-creative breakdown."""
        tz = ZoneInfo(settings.business_timezone)
        end = datetime.now(tz).date()
        start = end - timedelta(days=settings.roas_window_days)

        daily_stats = await self._db.get_stats_range(start, end)
        result = self.calculate_roas(daily_stats, settings.roas_window_days)

        # Per-creative breakdown from ad_creative_metrics (silently skipped if table empty)
        try:
            creative_rows = await self._db.get_creative_metrics_range(start, end)
            creative_breakdown = _aggregate_creatives(creative_rows)
        except Exception:
            logger.exception("Per-creative breakdown failed (non-fatal)")
            creative_breakdown = []

        await self._send_roas_slack(result, creative_breakdown)
        logger.info(
            "Weekly ROAS report: %.2fx over %d days (spend=$%.2f, rev=$%.2f, %d creatives)",
            result.roas,
            result.window_days,
            result.total_ad_spend,
            result.total_revenue,
            len(creative_breakdown),
        )
        return result

    async def _send_roas_slack(
        self, result: ROASResult, creative_breakdown: list[dict[str, Any]] | None = None,
    ) -> None:
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
                    {"type": "mrkdwn", "text": f"*MER:* {result.mer}x  _(the honest one)_"},
                    {"type": "mrkdwn", "text": f"*Platform ROAS:* {result.roas}x"},
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
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": "_MER = total revenue / total ad spend. Healthy DTC target: 3-5x. "
                            "Below 2x = something's leaking._",
                }],
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

        # Per-creative breakdown — only shown when we have data
        if creative_breakdown:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": "*Creative performance (last %d days)*" % result.window_days},
            })
            for c in creative_breakdown[:10]:
                blocks.append({"type": "section", "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"• *{c['name']}* — "
                        f"${c['spend']:,.2f} spent, "
                        f"{c['impressions']:,} imp, "
                        f"CTR {c['ctr']:.2f}%, "
                        f"{c['purchases']} 🛒"
                    ),
                }})

        await self._slack.send_blocks(blocks, text=f"Weekly Ads: MER {result.mer}x")
