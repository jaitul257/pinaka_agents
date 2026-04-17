"""Piece of the Quarter — quarterly email to past buyers (Phase 9.3).

Fires 4x/year (first Monday of Jan/Apr/Jul/Oct). One Claude-drafted email
celebrating a new/featured piece goes to every past buyer who accepts
marketing. Slack approval gate; batched SendGrid send.

Dedup philosophy (intentional): no per-quarter DB flag. If the cron fires
twice in one quarter, the founder sees two Slack prompts and skips the
duplicate. Simple, visible, reversible.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)


POQ_SYSTEM_PROMPT = """You draft the Piece of the Quarter email for Pinaka Jewellery — \
a quarterly note from founder Jaitul to every past buyer. These are people who already \
bought (averaging $4,500-$5,100 AOV) and trust the brand. You are not selling. You are \
sharing one new thing.

Content rules:
- 100-140 words.
- Open with what's new (a variant, a metal, a stone shape, a limited capsule, a seasonal cut).
- One sentence on why it's interesting — what problem it solves or who it's for.
- Never mention "in celebration of the season" or "just in time for spring" clichés.
- No discount codes. No urgency. No "reply to preorder" CTA — they can just visit the site.
- End with a light CTA: "Visit the journal" or "Reply if anything jumps out" — never "Buy now".
- Sign off: "Warm,\\nJaitul"
- Plain text, short paragraphs. The template handles the shell.

Output strict JSON: {"subject": "...", "body": "..."}"""


@dataclass
class QuarterlyDraft:
    subject: str
    body: str
    audience_count: int
    featured_piece: str
    quarter_key: str  # e.g. "2026-Q2"


class PieceOfQuarter:
    """Draft + send the quarterly email campaign."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    async def build_audience(self) -> list[dict[str, Any]]:
        """All customers who've bought at least once AND accept marketing."""
        client = self._db._sync._client
        import asyncio
        result = await asyncio.to_thread(
            lambda: (
                client.table("customers")
                .select("id, email, name, order_count")
                .gte("order_count", 1)
                .eq("accepts_marketing", True)
                .execute()
            )
        )
        return result.data or []

    async def pick_featured_piece(self) -> str:
        """Pick the featured product name — prefers top-spend ad_name last 14d,
        falls back to a random product from the catalog."""
        try:
            from datetime import date, timedelta
            rows = await self._db.get_creative_metrics_range(
                date.today() - timedelta(days=14), date.today(),
            )
            if rows:
                by_name: dict[str, float] = {}
                for r in rows:
                    name = r.get("ad_name") or r.get("creative_name") or ""
                    if name:
                        by_name[name] = by_name.get(name, 0) + float(r.get("spend") or 0)
                if by_name:
                    return max(by_name.items(), key=lambda kv: kv[1])[0]
        except Exception:
            logger.exception("POQ: top-creative lookup failed (non-fatal)")

        products = await self._db.get_all_products()
        if products:
            return products[0].get("name") or products[0].get("title") or "our newest piece"
        return "our newest piece"

    async def draft(self) -> QuarterlyDraft:
        audience = await self._build_audience_only()
        featured = await self.pick_featured_piece()
        quarter_key = _current_quarter_key()

        if not self._claude:
            return QuarterlyDraft(
                subject=f"Something new this quarter ({quarter_key})",
                body=_fallback_body(featured),
                audience_count=len(audience),
                featured_piece=featured,
                quarter_key=quarter_key,
            )

        user_prompt = (
            f"Featured piece this quarter: {featured}\n"
            f"Quarter: {quarter_key}\n"
            f"Audience size: {len(audience)} past buyers\n\n"
            "Draft the email now. Return JSON only."
        )
        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=600,
                system=POQ_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            parsed = json.loads(text[start_idx : end_idx + 1])
            subject = str(parsed.get("subject", "")).strip()[:90] or f"Something new this quarter"
            body = str(parsed.get("body", "")).strip() or _fallback_body(featured)
        except Exception:
            logger.exception("POQ Claude draft failed; using fallback")
            subject = f"Something new this quarter ({quarter_key})"
            body = _fallback_body(featured)

        return QuarterlyDraft(
            subject=subject, body=body,
            audience_count=len(audience),
            featured_piece=featured, quarter_key=quarter_key,
        )

    async def _build_audience_only(self) -> list[dict[str, Any]]:
        """Separate method so .audience_count in the draft is accurate."""
        return await self.build_audience()

    async def send_batch(self, subject: str, body: str) -> dict[str, Any]:
        """Send the approved campaign to every past buyer. Called from Slack approve handler."""
        from src.core.email import EmailSender
        audience = await self.build_audience()
        sender = EmailSender()
        sent = 0
        failed = 0
        for customer in audience:
            email_addr = customer.get("email") or ""
            name = customer.get("name") or email_addr
            if not email_addr:
                continue
            ok = sender.send_lifecycle_email(
                to_email=email_addr, customer_name=name,
                subject=subject, email_body=body,
            )
            if ok:
                sent += 1
            else:
                failed += 1
        logger.info("POQ batch sent: %d/%d delivered, %d failed", sent, len(audience), failed)
        return {"audience": len(audience), "sent": sent, "failed": failed}


def _current_quarter_key() -> str:
    from datetime import date
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q}"


def _fallback_body(featured: str) -> str:
    return (
        f"Something new this quarter — {featured}. "
        "We kept it simple: same craftsmanship, one element changed. "
        "If it's the right fit for someone you know (or for a future self), you'll know it when you see it.\n\n"
        "Visit the journal or reply if anything jumps out.\n\n"
        "Warm,\nJaitul"
    )
