"""Voice-of-customer weekly theme miner (Phase 10.C).

Every Monday 11 AM ET, pulls the last 7 days of customer text from three sources:
  1. `messages` — inbound email/support messages
  2. `concierge_chat_logs` if present — on-site chat transcripts
  3. `post_purchase_attribution.channel_detail` + `purchase_reason_detail` free-text

Claude clusters them into 3-5 themes with representative quotes and a source
tag. Posts to Slack; writes one row per week to `customer_insights`.

Why this exists: individual messages come through Slack as they happen and get
scrolled past. Weekly aggregation turns noise into product signal.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are reading a week's worth of raw customer text for Pinaka Jewellery \
(handcrafted diamond tennis bracelets, $4,500-$5,100 AOV). Your job is to cluster everything \
into 3-5 meaningful themes — things the founder should actually hear.

Rules:
- A theme is actionable. "People asked about shipping" is weak. "Three buyers asked how to \
size the bracelet after gift-giving" is strong — it names a product gap.
- Every theme must be backed by ≥2 mentions. Solo mentions get skipped (too noisy).
- Quote the customer directly. One representative quote per theme.
- If the week's data is thin (under 5 total sources), say so honestly and return fewer themes.
- No generic business-speak ("enhance the customer journey"). Use words a friend would use.
- Tag each theme's dominant source: "support_email", "concierge_chat", or "post_purchase_survey".

Output strict JSON only:
{
  "themes": [
    {
      "theme": "short label, 4-8 words",
      "description": "one sentence explaining the pattern + what it implies",
      "representative_quote": "exact customer quote",
      "count": 3,
      "source": "support_email | concierge_chat | post_purchase_survey",
      "suggested_action": "one sentence — what should change on site, copy, product, or process"
    }
  ]
}"""


@dataclass
class VOCResult:
    week_ending: date
    themes: list[dict[str, Any]]
    messages_analyzed: int
    chats_analyzed: int
    survey_responses: int


class VoiceOfCustomer:
    """Weekly theme miner for all customer text."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._slack = SlackNotifier()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    async def run_weekly(self) -> VOCResult:
        tz = ZoneInfo(settings.business_timezone)
        today = datetime.now(tz).date()
        week_start = today - timedelta(days=7)
        week_end = today - timedelta(days=1)

        messages = await self._load_messages(week_start, week_end)
        chats = await self._load_chat_logs(week_start, week_end)
        surveys = await self._load_survey_text(week_start, week_end)

        total_sources = len(messages) + len(chats) + len(surveys)
        themes: list[dict[str, Any]] = []
        if total_sources >= 2 and self._claude:
            themes = await self._mine_themes(messages, chats, surveys)

        result = VOCResult(
            week_ending=week_end,
            themes=themes,
            messages_analyzed=len(messages),
            chats_analyzed=len(chats),
            survey_responses=len(surveys),
        )

        await self._persist(result)
        await self._post_slack(result)
        return result

    async def _load_messages(self, start: date, end: date) -> list[dict[str, Any]]:
        client = self._db._sync._client
        import asyncio
        try:
            resp = await asyncio.to_thread(
                lambda: (
                    client.table("messages")
                    .select("buyer_email,subject,body,category,created_at")
                    .gte("created_at", start.isoformat())
                    .lte("created_at", end.isoformat())
                    .execute()
                )
            )
            return resp.data or []
        except Exception:
            logger.exception("VOC: messages load failed")
            return []

    async def _load_chat_logs(self, start: date, end: date) -> list[dict[str, Any]]:
        """Concierge chat transcripts if the table exists. Silent fail if not."""
        client = self._db._sync._client
        import asyncio
        try:
            resp = await asyncio.to_thread(
                lambda: (
                    client.table("concierge_chat_logs")
                    .select("session_id,user_message,assistant_reply,created_at")
                    .gte("created_at", start.isoformat())
                    .lte("created_at", end.isoformat())
                    .execute()
                )
            )
            return resp.data or []
        except Exception:
            # Table likely doesn't exist yet — not an error
            return []

    async def _load_survey_text(self, start: date, end: date) -> list[dict[str, Any]]:
        client = self._db._sync._client
        import asyncio
        try:
            resp = await asyncio.to_thread(
                lambda: (
                    client.table("post_purchase_attribution")
                    .select("channel_primary,channel_detail,purchase_reason,purchase_reason_detail,created_at")
                    .gte("created_at", start.isoformat())
                    .lte("created_at", end.isoformat())
                    .execute()
                )
            )
            rows = resp.data or []
            # Keep only rows that have actual free-text content
            return [
                r for r in rows
                if (r.get("channel_detail") and r["channel_detail"].strip())
                or (r.get("purchase_reason_detail") and r["purchase_reason_detail"].strip())
            ]
        except Exception:
            logger.exception("VOC: survey load failed")
            return []

    async def _mine_themes(
        self,
        messages: list[dict[str, Any]],
        chats: list[dict[str, Any]],
        surveys: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Flatten to one big labeled payload for Claude
        snippets: list[str] = []
        for m in messages[:60]:
            body = (m.get("body") or "")[:400]
            subject = m.get("subject") or ""
            if body:
                snippets.append(f"[support_email | {subject}] {body}")
        for c in chats[:80]:
            user = (c.get("user_message") or "")[:300]
            if user:
                snippets.append(f"[concierge_chat] {user}")
        for s in surveys[:60]:
            detail = (s.get("channel_detail") or "").strip()
            reason = (s.get("purchase_reason_detail") or "").strip()
            if detail:
                snippets.append(f"[post_purchase_survey | channel] {detail}")
            if reason:
                snippets.append(f"[post_purchase_survey | reason] {reason}")

        if not snippets:
            return []

        user_prompt = (
            f"Raw customer text from the last week ({len(snippets)} snippets total):\n\n"
            + "\n".join(f"- {s}" for s in snippets)
            + "\n\nReturn strict JSON with 3-5 themes. Each backed by ≥2 mentions. "
              "If fewer than 5 sources here, return 1-2 themes max."
        )
        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx < 0 or end_idx <= start_idx:
                return []
            parsed = json.loads(text[start_idx : end_idx + 1])
            themes = parsed.get("themes", [])
            return themes[:5]
        except Exception:
            logger.exception("VOC: Claude theme mining failed")
            return []

    async def _persist(self, result: VOCResult) -> None:
        row = {
            "week_ending": result.week_ending.isoformat(),
            "themes": result.themes,
            "messages_analyzed": result.messages_analyzed,
            "chats_analyzed": result.chats_analyzed,
            "survey_responses": result.survey_responses,
            "sources": {
                "support_email": result.messages_analyzed,
                "concierge_chat": result.chats_analyzed,
                "post_purchase_survey": result.survey_responses,
            },
        }
        client = self._db._sync._client
        import asyncio
        try:
            await asyncio.to_thread(
                lambda: (
                    client.table("customer_insights")
                    .upsert(row, on_conflict="week_ending")
                    .execute()
                )
            )
        except Exception:
            logger.exception("VOC: failed to persist customer_insights row")

    async def _post_slack(self, result: VOCResult) -> None:
        if not result.themes:
            await self._slack.send_blocks([
                {"type": "header", "text": {"type": "plain_text", "text": ":ear: Voice of Customer"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": (f"Week ending *{result.week_ending.isoformat()}* — too few signals to cluster. "
                             f"{result.messages_analyzed} emails, {result.chats_analyzed} chats, "
                             f"{result.survey_responses} surveys. Normal at our volume.")}}
            ], text="VOC: thin week")
            return

        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": ":ear: Voice of Customer"}},
            {"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": (f"Week of *{result.week_ending.isoformat()}* — "
                         f"{result.messages_analyzed} email · {result.chats_analyzed} chat · "
                         f"{result.survey_responses} survey"),
            }]},
            {"type": "divider"},
        ]

        for t in result.themes:
            theme = t.get("theme") or "Unnamed theme"
            desc = t.get("description") or ""
            quote = (t.get("representative_quote") or "").strip()
            count = t.get("count") or "—"
            source = t.get("source") or ""
            action = t.get("suggested_action") or ""
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{theme}* — {count} mentions · `{source}`\n"
                    f"{desc}\n"
                    f"> _\"{quote}\"_\n"
                    f":point_right: {action}"
                ),
            }})

        await self._slack.send_blocks(blocks, text=f"VOC: {len(result.themes)} themes")
