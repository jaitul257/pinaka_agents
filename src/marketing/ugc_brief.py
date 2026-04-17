"""Weekly UGC brief generator.

Every Sunday 6 PM ET, Claude drafts 3 phone-shot video prompts the founder
can film during the week. Each brief has a specific setup, angle, and
30-second script beat so the founder doesn't have to invent the concept.

Context fed to Claude:
- Current seasonal window (Valentine's, Mother's Day, etc.)
- Recent products (1-3 representative SKUs with metal + carats)
- Top-performing creative archetype from last 14 days (if enough data)

Output: Slack Block Kit message with 3 numbered briefs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import anthropic

from src.agents.marketing import SEASONAL_CALENDAR
from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are drafting a weekly UGC filming brief for Jaitul, the solo founder of \
Pinaka Jewellery — premium handcrafted diamond tennis bracelets ($4,500-$5,100). \
He films phone videos on weekends to fuel Meta + Instagram ads.

Your job: generate exactly 3 video briefs for this week. Each brief must be:
- **Shootable on an iPhone in under 10 minutes** (no studio, no crew)
- **Authentic, not polished** — founder-led UGC outperforms studio glam 2-3x at our AOV
- **Specific** — "hold the bracelet under window light" not "show the bracelet nicely"
- **Brand-aligned** — warm, craft-led, self-purchase-positive (not gift-cliché)
- **Different archetype each** — pick from: style-reaction, atelier-process, \
founder-talking-head, unboxing-pov, wrist-in-the-wild, comparison-tell

Forbidden:
- Generic lifestyle tropes (brunch, beach, hotel bed)
- "Ultra-detailed, cinematic, stunning" buzzword language
- Discount/promo hooks
- More than 30 seconds per video
- Requiring a tripod or ring light (iPhone handheld only)

Output format: strict JSON only, no prose before/after.
{
  "briefs": [
    {
      "title": "short memorable label, <=6 words",
      "archetype": "one of: style-reaction | atelier-process | founder-talking-head | unboxing-pov | wrist-in-the-wild | comparison-tell",
      "setup": "one sentence about where to film + what to have on hand",
      "script_beats": ["beat 1 (under 5s)", "beat 2 (under 10s)", "beat 3 (under 15s)"],
      "hook_line": "the exact first line the founder should say or caption text",
      "why": "one sentence on why this works for our audience right now"
    }
  ]
}"""


@dataclass
class UGCBriefResult:
    briefs: list[dict[str, Any]]
    seasonal_window: str | None
    top_archetype_last_14d: str | None


class UGCBriefGenerator:
    """Compose a weekly UGC brief and post it to Slack."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._slack = SlackNotifier()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    async def run_weekly(self) -> UGCBriefResult:
        context = await self._build_context()
        briefs = await self._generate(context)
        await self._post_slack(briefs, context)
        return UGCBriefResult(
            briefs=briefs,
            seasonal_window=context.get("seasonal_window"),
            top_archetype_last_14d=context.get("top_archetype"),
        )

    async def _build_context(self) -> dict[str, Any]:
        today = date.today()

        # Seasonal window — reuse the calendar the marketing agent reads
        seasonal = None
        for win in SEASONAL_CALENDAR:
            start = date(today.year, win["start"][0], win["start"][1])
            end = date(today.year, win["end"][0], win["end"][1])
            if start <= today <= end:
                seasonal = {
                    "name": win["name"],
                    "angle": win["angle"],
                    "days_left": (end - today).days,
                }
                break

        # 3 recent products (from Supabase — skip if empty)
        products: list[dict[str, Any]] = []
        try:
            all_products = await self._db.get_all_products()
            for p in (all_products or [])[:3]:
                products.append({
                    "sku": p.get("sku", ""),
                    "name": p.get("name", ""),
                    "story": (p.get("story") or "")[:300],
                    "materials": p.get("materials", {}),
                })
        except Exception:
            logger.exception("Could not load products for UGC brief (non-fatal)")

        # Top ad_name from the last 14 days by spend — used as hint only
        top_archetype = None
        try:
            rows = await self._db.get_creative_metrics_range(
                today - timedelta(days=14), today
            )
            if rows:
                by_ad: dict[str, float] = {}
                for r in rows:
                    name = r.get("ad_name") or r.get("creative_name") or ""
                    if name:
                        by_ad[name] = by_ad.get(name, 0) + float(r.get("spend") or 0)
                if by_ad:
                    top_archetype = max(by_ad.items(), key=lambda kv: kv[1])[0]
        except Exception:
            logger.exception("Could not load creative metrics for UGC brief (non-fatal)")

        return {
            "seasonal_window": seasonal["name"] if seasonal else None,
            "seasonal_angle": seasonal["angle"] if seasonal else None,
            "days_left_in_window": seasonal["days_left"] if seasonal else None,
            "products": products,
            "top_archetype": top_archetype,
        }

    async def _generate(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        if not self._claude:
            logger.warning("Anthropic key missing — skipping UGC brief generation")
            return []

        user_prompt = (
            "Context for this week's briefs:\n\n"
            f"Seasonal window: {context.get('seasonal_window') or 'None'}\n"
            f"Seasonal angle: {context.get('seasonal_angle') or 'N/A'}\n"
            f"Days left in window: {context.get('days_left_in_window') or 'N/A'}\n\n"
            f"Top-spending ad name (last 14d): {context.get('top_archetype') or 'No data yet'}\n\n"
            "Recent products:\n"
            + json.dumps(context.get("products", []), indent=2)
            + "\n\nReturn exactly 3 briefs as JSON. Each archetype must be different."
        )

        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=1800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx < 0 or end_idx <= start_idx:
                logger.error("UGC brief: no JSON in Claude response")
                return []
            parsed = json.loads(text[start_idx:end_idx + 1])
            briefs = parsed.get("briefs", [])
            return briefs[:3]
        except Exception:
            logger.exception("UGC brief Claude call failed")
            return []

    async def _post_slack(
        self, briefs: list[dict[str, Any]], context: dict[str, Any]
    ) -> None:
        if not briefs:
            await self._slack.send_blocks([
                {"type": "header", "text": {"type": "plain_text", "text": ":video_camera: Weekly UGC Brief"}},
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    "_Could not generate this week. Check logs — likely missing Anthropic key or no products in DB._"}},
            ], text="Weekly UGC Brief (empty)")
            return

        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": ":video_camera: Film this week"}},
        ]
        if context.get("seasonal_window"):
            blocks.append({"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": f"*{context['seasonal_window']}* season — {context.get('days_left_in_window')} days left. "
                        f"Angle: _{context.get('seasonal_angle')}_",
            }]})
        blocks.append({"type": "divider"})

        for i, brief in enumerate(briefs, 1):
            title = brief.get("title", f"Brief {i}")
            archetype = brief.get("archetype", "")
            setup = brief.get("setup", "")
            beats = brief.get("script_beats", []) or []
            hook = brief.get("hook_line", "")
            why = brief.get("why", "")

            beats_text = "\n".join(f"  _{b}_" for b in beats[:3])
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{i}. {title}* — `{archetype}`\n"
                    f"*Setup:* {setup}\n"
                    f"*Hook:* \"{hook}\"\n"
                    f"*Beats:*\n{beats_text}\n"
                    f"_{why}_"
                ),
            }})
            if i < len(briefs):
                blocks.append({"type": "divider"})

        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": "_Phone handheld, natural light, 30s max each. Upload to `/dashboard/ad-creatives` "
                    "when done — they're auto-queued as Variant candidates._",
        }]})

        await self._slack.send_blocks(blocks, text=f"Film this week: {len(briefs)} briefs")
