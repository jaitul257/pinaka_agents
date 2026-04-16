"""Weekly attribution synthesizer.

Pulls last-7-days post-purchase survey responses and produces a Slack-ready
summary of where customers actually came from — the ground truth that Meta
and GA4 can't give us at long consideration windows.

One Claude call per week. Free-text details are clustered; multi-choice
channel tallies are pure SQL.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


CHANNEL_LABELS = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "pinterest": "Pinterest",
    "google_search": "Google search",
    "meta_ads": "Meta ad",
    "podcast": "Podcast",
    "press": "Press / podcast",
    "friend": "Friend / family",
    "other": "Other",
}

REASON_LABELS = {
    "self_purchase": "self-purchase",
    "gift": "gift",
    "anniversary": "anniversary",
    "engagement": "engagement",
    "milestone": "milestone",
    "other": "other",
}


SYNTH_PROMPT = """You are reviewing the past week's post-purchase attribution survey responses \
for Pinaka Jewellery. The multi-choice tallies are already computed. Your job is to read the \
free-text detail field and produce 2-3 short, specific observations.

Rules:
- If a creator/publication/podcast name appears more than once, flag it.
- If multiple buyers mention a specific ad, creative, or landing page, flag it.
- If the free-text is empty or only generic, say so honestly — do not fabricate trends.
- Never invent a pattern from a single response.
- Be concise: each observation is one sentence. 3 max. No preamble.

Return JSON only: {"observations": ["...", "..."]}"""


@dataclass
class AttributionSummary:
    window_days: int
    total_responses: int
    channel_counts: dict[str, int]
    channel_percentages: dict[str, float]
    reason_counts: dict[str, int]
    free_text_details: list[str]
    ai_observations: list[str]


class AttributionSynthesizer:
    """Weekly summarizer for post-purchase attribution responses."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._slack = SlackNotifier()
        self._claude = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    async def run_weekly_report(self, window_days: int = 7) -> AttributionSummary:
        """Build summary for the last N days and post to Slack."""
        tz = ZoneInfo(settings.business_timezone)
        end = datetime.now(tz).date()
        start = end - timedelta(days=window_days)

        rows = await self._db.get_attribution_range(start, end)
        summary = self._aggregate(rows, window_days)
        summary.ai_observations = await self._synthesize_free_text(summary.free_text_details)

        await self._post_slack(summary, start, end)
        logger.info(
            "Attribution report: %d responses over %d days; top channel: %s",
            summary.total_responses, window_days,
            next(iter(summary.channel_counts), "—"),
        )
        return summary

    def _aggregate(self, rows: list[dict[str, Any]], window_days: int) -> AttributionSummary:
        channel_counts = Counter(r["channel_primary"] for r in rows if r.get("channel_primary"))
        reason_counts = Counter(r["purchase_reason"] for r in rows if r.get("purchase_reason"))
        total = len(rows)

        channel_pct = {
            k: round(v / total * 100, 1) for k, v in channel_counts.items()
        } if total else {}

        details = [
            r["channel_detail"].strip()
            for r in rows
            if r.get("channel_detail") and r["channel_detail"].strip()
        ]

        return AttributionSummary(
            window_days=window_days,
            total_responses=total,
            channel_counts=dict(channel_counts.most_common()),
            channel_percentages=channel_pct,
            reason_counts=dict(reason_counts.most_common()),
            free_text_details=details,
            ai_observations=[],
        )

    async def _synthesize_free_text(self, details: list[str]) -> list[str]:
        """Cluster free-text details via Claude. Returns [] on empty or error."""
        if not details or not self._claude:
            return []

        payload = "\n".join(f"- {d}" for d in details[:100])
        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=400,
                system=SYNTH_PROMPT,
                messages=[{"role": "user", "content": f"Free-text details from survey:\n{payload}"}],
            )
            text = response.content[0].text.strip()
            # Extract JSON (may have stray text)
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx >= 0 and end_idx > start_idx:
                parsed = json.loads(text[start_idx : end_idx + 1])
                obs = parsed.get("observations", [])
                return [str(o).strip() for o in obs if o][:3]
        except Exception:
            logger.exception("Attribution synth Claude call failed")
        return []

    async def _post_slack(self, summary: AttributionSummary, start: date, end: date) -> None:
        if summary.total_responses == 0:
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": ":mag: Weekly Attribution (no responses)"}},
                {"type": "section", "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"No post-purchase survey responses from {start.isoformat()} to {end.isoformat()}.\n"
                        "Either we had no orders this week, or the survey isn't rendering on the thank-you page. "
                        "Check Shopify admin → Settings → Checkout → Order status page → Additional scripts."
                    ),
                }},
            ]
            await self._slack.send_blocks(blocks, text="Weekly Attribution: no responses")
            return

        # Channel breakdown
        channel_lines = [
            f"• *{CHANNEL_LABELS.get(k, k.title())}:* {v} ({summary.channel_percentages.get(k, 0)}%)"
            for k, v in summary.channel_counts.items()
        ]

        # Reason breakdown
        reason_lines = [
            f"• *{REASON_LABELS.get(k, k).title()}:* {v}"
            for k, v in summary.reason_counts.items()
        ] if summary.reason_counts else ["_Not asked / no data_"]

        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": ":mag: Weekly Attribution — Ground Truth"}},
            {"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": f"*{summary.total_responses} responses* from {start.isoformat()} to {end.isoformat()}",
            }]},
            {"type": "section", "text": {
                "type": "mrkdwn",
                "text": "*How buyers heard about us*\n" + "\n".join(channel_lines),
            }},
            {"type": "section", "text": {
                "type": "mrkdwn",
                "text": "*What they bought it for*\n" + "\n".join(reason_lines),
            }},
        ]

        if summary.ai_observations:
            blocks.append({"type": "divider"})
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn",
                "text": "*Observations from free-text*\n" + "\n".join(f"• {o}" for o in summary.ai_observations),
            }})

        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": "_Override for Meta/GA4 last-click. Reallocate budget to what actually brought them in._",
        }]})

        await self._slack.send_blocks(
            blocks,
            text=f"Weekly Attribution: {summary.total_responses} responses",
        )
