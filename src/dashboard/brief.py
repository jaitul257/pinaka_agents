"""Daily AI brief — the founder's single pane of glass.

Aggregates signals across Phase 9.0-9.2:
  - MER trend (14d) from daily_stats
  - Top/weak creatives from ad_creative_metrics
  - Open observations from the heartbeat awareness layer
  - Seasonal window + days-left
  - Pending lifecycle candidates count
  - Next unwritten SEO topic

Runs Claude once per render to produce a 3-paragraph "focus today"
narrative. Pure read; no mutations. Rendered at /dashboard/brief.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import anthropic

from src.agents.marketing import SEASONAL_CALENDAR
from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)

BRIEF_SYSTEM_PROMPT = """You write the morning focus brief for the solo founder of Pinaka Jewellery. \
You have the week's numbers, top/bottom creatives, open observations, and seasonal state. \
Your job: 3 short paragraphs. No more.

Paragraph 1: What matters today — one clear headline of the most important signal right now.
Paragraph 2: What's working + what's not — pick ONE thing compounding and ONE thing leaking.
Paragraph 3: One concrete action for today — specific enough to start in 5 minutes.

Rules:
- 80-120 words total. Short sentences. No em dashes, no "In summary", no bullet points.
- Never mention "AI" or "the agent".
- If data is sparse (new system, low volume), say so honestly and point to the next milestone.
- MER > 3x = healthy. Below 2x = leaking. Call it out.
- No motivational filler. No "keep it up".

Write in first person from the agent's perspective addressing the founder as "you"."""


@dataclass
class BriefData:
    generated_at: datetime
    window_days: int = 14
    # Money
    mer_14d: float | None = None
    revenue_14d: float = 0.0
    spend_14d: float = 0.0
    # Creatives
    top_creatives: list[dict[str, Any]] = field(default_factory=list)
    weak_creatives: list[dict[str, Any]] = field(default_factory=list)
    creative_count: int = 0
    # Awareness
    open_observations: list[dict[str, Any]] = field(default_factory=list)
    warning_count: int = 0
    critical_count: int = 0
    # Calendar
    seasonal_window: str | None = None
    seasonal_angle: str | None = None
    days_left_in_window: int | None = None
    # Pipeline
    pending_lifecycle_candidates: int = 0
    new_welcome_cohort_count: int = 0
    # Content
    next_seo_topic: str | None = None
    # Customer intelligence (Phase 10)
    segment_counts: dict[str, int] = field(default_factory=dict)
    total_customers: int = 0
    voc_themes: list[dict[str, Any]] = field(default_factory=list)
    voc_week_ending: str | None = None
    # Narrative
    narrative: str = ""


class DashboardBrief:
    """Build the daily brief on demand."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    async def build(self, window_days: int = 14) -> BriefData:
        today = date.today()
        start = today - timedelta(days=window_days)

        brief = BriefData(generated_at=datetime.now(timezone.utc), window_days=window_days)

        await self._load_money(brief, start, today)
        await self._load_creatives(brief, start, today)
        await self._load_observations(brief)
        self._load_seasonal(brief, today)
        await self._load_pipeline(brief)
        await self._load_customer_intelligence(brief)
        brief.next_seo_topic = _next_seo_topic_guess()
        brief.narrative = await self._narrate(brief)

        logger.info(
            "Brief built: MER=%s, creatives=%d, obs=%d, pending_lifecycle=%d",
            brief.mer_14d, brief.creative_count,
            len(brief.open_observations), brief.pending_lifecycle_candidates,
        )
        return brief

    async def _load_money(self, brief: BriefData, start: date, end: date) -> None:
        try:
            stats = await self._db.get_stats_range(start, end)
            total_spend = sum(
                float(s.get("ad_spend_google", 0)) + float(s.get("ad_spend_meta", 0))
                for s in stats or []
            )
            total_revenue = sum(float(s.get("revenue", 0)) for s in stats or [])
            brief.spend_14d = round(total_spend, 2)
            brief.revenue_14d = round(total_revenue, 2)
            brief.mer_14d = round(total_revenue / total_spend, 2) if total_spend > 0 else None
        except Exception:
            logger.exception("Brief: money load failed")

    async def _load_creatives(self, brief: BriefData, start: date, end: date) -> None:
        try:
            rows = await self._db.get_creative_metrics_range(start, end)
            by_ad = _aggregate_by_ad(rows or [])
            brief.creative_count = len(by_ad)
            # Top by spend (most-invested), showing what the budget actually bought
            brief.top_creatives = sorted(by_ad, key=lambda a: a["spend"], reverse=True)[:3]
            # Weak = >=500 impressions AND CTR < 0.8% (below the floor for healthy)
            weak = [a for a in by_ad if a["impressions"] >= 500 and a["ctr"] < 0.8]
            brief.weak_creatives = sorted(weak, key=lambda a: a["ctr"])[:3]
        except Exception:
            logger.exception("Brief: creatives load failed")

    async def _load_observations(self, brief: BriefData) -> None:
        try:
            client = self._db._sync._client
            import asyncio
            result = await asyncio.to_thread(
                lambda: (
                    client.table("observations")
                    .select("*")
                    .eq("acted_on", False)
                    .in_("severity", ["warning", "critical"])
                    .order("created_at", desc=True)
                    .limit(8)
                    .execute()
                )
            )
            data = result.data or []
            brief.open_observations = data[:5]
            brief.warning_count = sum(1 for o in data if o.get("severity") == "warning")
            brief.critical_count = sum(1 for o in data if o.get("severity") == "critical")
        except Exception:
            logger.exception("Brief: observations load failed")

    def _load_seasonal(self, brief: BriefData, today: date) -> None:
        for win in SEASONAL_CALENDAR:
            start = date(today.year, win["start"][0], win["start"][1])
            end = date(today.year, win["end"][0], win["end"][1])
            if start <= today <= end:
                brief.seasonal_window = win["name"]
                brief.seasonal_angle = win["angle"]
                brief.days_left_in_window = (end - today).days
                return

    async def _load_customer_intelligence(self, brief: BriefData) -> None:
        """Segment counts from customer_rfm + latest VOC themes."""
        # Segment distribution — uses customers.last_segment (pointer updated by RFM cron)
        try:
            client = self._db._sync._client
            import asyncio
            resp = await asyncio.to_thread(
                lambda: client.table("customers").select("last_segment").execute()
            )
            counts: dict[str, int] = {}
            total = 0
            for row in resp.data or []:
                seg = row.get("last_segment")
                total += 1
                if seg:
                    counts[seg] = counts.get(seg, 0) + 1
            brief.segment_counts = counts
            brief.total_customers = total
        except Exception:
            logger.exception("Brief: segment counts load failed")

        # Latest VOC themes (one row per week in customer_insights)
        try:
            client = self._db._sync._client
            import asyncio
            resp = await asyncio.to_thread(
                lambda: (
                    client.table("customer_insights")
                    .select("week_ending,themes")
                    .order("week_ending", desc=True)
                    .limit(1)
                    .execute()
                )
            )
            if resp.data:
                row = resp.data[0]
                brief.voc_week_ending = str(row.get("week_ending") or "")
                brief.voc_themes = row.get("themes") or []
        except Exception:
            logger.exception("Brief: VOC themes load failed")

    async def _load_pipeline(self, brief: BriefData) -> None:
        try:
            from src.customer.lifecycle import LifecycleOrchestrator
            orch = LifecycleOrchestrator()
            candidates = await orch.find_all_candidates()
            brief.pending_lifecycle_candidates = len(candidates)
        except Exception:
            logger.exception("Brief: pipeline load failed")

        try:
            welcome = await self._db.get_welcome_candidates()
            brief.new_welcome_cohort_count = len(welcome or [])
        except Exception:
            logger.exception("Brief: welcome cohort load failed")

    async def _narrate(self, brief: BriefData) -> str:
        if not self._claude:
            return _fallback_narrative(brief)
        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=400,
                system=BRIEF_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _narration_input(brief)}],
            )
            return response.content[0].text.strip()
        except Exception:
            logger.exception("Brief: narrative Claude call failed")
            return _fallback_narrative(brief)


# ── Helpers ──

def _aggregate_by_ad(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import defaultdict
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "name": "", "impressions": 0, "clicks": 0, "spend": 0.0,
        "purchases": 0,
    })
    for r in rows:
        key = r.get("meta_ad_id") or ""
        if not key:
            continue
        item = agg[key]
        if not item["name"]:
            item["name"] = r.get("ad_name") or r.get("creative_name") or key
        item["impressions"] += int(r.get("impressions") or 0)
        item["clicks"] += int(r.get("clicks") or 0)
        item["spend"] += float(r.get("spend") or 0)
        item["purchases"] += int(r.get("purchase_count") or 0)
    out: list[dict[str, Any]] = []
    for item in agg.values():
        if item["impressions"] == 0:
            continue
        item["ctr"] = round(item["clicks"] / item["impressions"] * 100, 2)
        item["spend"] = round(item["spend"], 2)
        out.append(item)
    return out


def _next_seo_topic_guess() -> str | None:
    """Rough heuristic — the full rotation lives in src/content/seo_writer.py.
    Here we surface the "likely next topic" without doing the full DB lookup
    so the brief page stays cheap to render."""
    from src.content.seo_writer import SEO_KEYWORDS
    return SEO_KEYWORDS[0] if SEO_KEYWORDS else None


def _narration_input(brief: BriefData) -> str:
    top = "\n".join(
        f"  - {c['name']}: ${c['spend']:,.0f}, {c['impressions']:,} imp, CTR {c['ctr']}%, {c['purchases']} 🛒"
        for c in brief.top_creatives
    ) or "  (no creative metrics yet — cron hasn't populated the table)"
    weak = "\n".join(
        f"  - {c['name']}: CTR {c['ctr']}%, ${c['spend']:,.0f} spent"
        for c in brief.weak_creatives
    ) or "  (none flagged — either all healthy or too little data)"
    obs = "\n".join(
        f"  - [{o.get('severity')}] {o.get('summary', '')[:160]}"
        for o in brief.open_observations
    ) or "  (no open warnings)"
    return f"""14-day window ending today.

Money:
  Revenue: ${brief.revenue_14d:,.2f}
  Ad spend: ${brief.spend_14d:,.2f}
  MER: {brief.mer_14d or 'N/A'}x

Top creatives (by spend):
{top}

Weak creatives (CTR < 0.8% on 500+ imp):
{weak}

Open observations ({brief.critical_count} critical, {brief.warning_count} warning):
{obs}

Seasonal: {brief.seasonal_window or 'No active window'} \
{f"({brief.days_left_in_window}d left)" if brief.days_left_in_window else ''}
Pending lifecycle emails awaiting approval: {brief.pending_lifecycle_candidates}
Welcome cohort customers waiting for next step: {brief.new_welcome_cohort_count}
Next SEO topic up: {brief.next_seo_topic or 'rotation empty'}

Customer segments ({brief.total_customers} total): {brief.segment_counts or 'not yet computed'}
Voice-of-customer themes this week: {', '.join((t.get('theme','—') for t in brief.voc_themes[:3])) or 'no themes yet'}"""


def _fallback_narrative(brief: BriefData) -> str:
    """Deterministic summary when Claude is unavailable."""
    parts = []
    if brief.mer_14d is None:
        parts.append("No ad spend recorded in the last 14 days.")
    elif brief.mer_14d >= 3.0:
        parts.append(f"MER is {brief.mer_14d}x over 14 days — healthy.")
    elif brief.mer_14d >= 2.0:
        parts.append(f"MER is {brief.mer_14d}x — maintain, don't scale yet.")
    else:
        parts.append(f"MER is {brief.mer_14d}x — something's leaking, audit creatives.")

    if brief.pending_lifecycle_candidates > 0:
        parts.append(
            f"{brief.pending_lifecycle_candidates} lifecycle emails waiting in Slack."
        )
    if brief.critical_count > 0:
        parts.append(f"{brief.critical_count} critical observation(s) open.")

    return " ".join(parts) or "No signals today."
