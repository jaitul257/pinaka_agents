"""Weekly competitor watch — what are Vrai/Catbird/Mejuri/Aurate/Mateo doing?

Uses Anthropic's native WebSearch tool instead of scraping. Claude does ~2-3
searches per brand, synthesizes into 3-5 actionable observations, and posts
to Slack Mondays 10 AM ET.

Cost: ~$0.15-0.25/week (WebSearch tool calls + Claude tokens). Cheaper and
vastly more robust than HTML scraping Meta Ad Library.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


# The reference set, ranked by our actual competitive relevance.
# Vrai + Brilliant Earth = lab-grown positioning peers.
# Catbird + Mejuri + Aurate = indie-luxury DTC peers.
# Mateo = high-AOV artisanal peer.
DEFAULT_COMPETITORS = [
    {"name": "Vrai", "url": "vrai.com", "angle": "lab-grown diamonds"},
    {"name": "Catbird", "url": "catbird.com", "angle": "indie NYC editorial luxury"},
    {"name": "Mejuri", "url": "mejuri.com", "angle": "fine jewelry every day"},
    {"name": "Aurate", "url": "auratenewyork.com", "angle": "DTC fine jewelry"},
    {"name": "Mateo", "url": "mateonewyork.com", "angle": "high-AOV artisanal"},
]


SYSTEM_PROMPT = """You are a competitive intelligence analyst for Pinaka Jewellery — \
a premium handcrafted diamond tennis bracelet brand ($4,500-$5,100 AOV, DTC). \
Every Monday you produce a brief on what direct competitors are doing NOW.

You have access to the web_search tool. Use it to check each competitor. Look for:

1. **Homepage hero** — what's the current headline/angle? (Any recent change from the last brief is signal.)
2. **Active campaigns** — new landing pages, drops, collabs, or seasonal pushes.
3. **Recent press / mentions** — any editorial placements, podcast features, notable customers.
4. **Messaging shifts** — positioning moves (e.g. sustainability push, new hero story, founder-led content).
5. **Pricing / promo tells** — visible discounts, bundle offers, free-shipping thresholds (context vs our $75/day paid spend).

DO NOT write a summary of each brand. Instead, output 3-5 SHARP observations across the whole set — the ones most relevant to Pinaka's $5K AOV, tennis-bracelet, handcrafted-Indian-heritage position.

Each observation: one sentence on WHAT they're doing + one sentence on WHY IT MATTERS for Pinaka + one concrete WHAT-TO-DO (test, ignore, double down on opposite, etc.).

Return strict JSON only:
{
  "observations": [
    {
      "who": "brand name (or 'Multiple')",
      "what": "what they're doing right now",
      "why_matters": "why this is signal for Pinaka",
      "action": "what Pinaka should do — test / ignore / counter-position / borrow"
    }
  ]
}

Aim for 3-5 observations. Quality over quantity. If the web is thin this week, return fewer observations and say so honestly in the first one."""


@dataclass
class CompetitorBriefResult:
    observations: list[dict[str, Any]]


class CompetitorBrief:
    """Weekly competitor intelligence brief via Claude + WebSearch."""

    def __init__(self, competitors: list[dict[str, str]] | None = None):
        self._competitors = competitors or DEFAULT_COMPETITORS
        self._slack = SlackNotifier()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    async def run_weekly(self) -> CompetitorBriefResult:
        observations = await self._ask_claude()
        await self._post_slack(observations)
        return CompetitorBriefResult(observations=observations)

    async def _ask_claude(self) -> list[dict[str, Any]]:
        if not self._claude:
            logger.warning("Anthropic key missing — skipping competitor brief")
            return []

        brand_list = "\n".join(
            f"- {c['name']} ({c['url']}) — {c['angle']}"
            for c in self._competitors
        )
        user_prompt = (
            f"Competitors to survey this week:\n{brand_list}\n\n"
            "Use web_search (up to 3 queries per brand max). Then return "
            "3-5 JSON observations as specified."
        )

        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=2500,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 12,
                }],
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception:
            logger.exception("Competitor brief Claude call failed")
            return []

        # Claude may emit multiple content blocks (tool uses, tool results, text).
        # We want the FINAL text block that contains the JSON.
        final_text = ""
        for block in response.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                final_text = block.text
        if not final_text:
            logger.warning("Competitor brief: no text block in Claude response")
            return []

        import json as _json
        start_idx = final_text.find("{")
        end_idx = final_text.rfind("}")
        if start_idx < 0 or end_idx <= start_idx:
            logger.warning("Competitor brief: no JSON in text block")
            return []
        try:
            parsed = _json.loads(final_text[start_idx:end_idx + 1])
            return list(parsed.get("observations", []))[:5]
        except Exception:
            logger.exception("Competitor brief: could not parse JSON")
            return []

    async def _post_slack(self, observations: list[dict[str, Any]]) -> None:
        tz = ZoneInfo(settings.business_timezone)
        week_label = datetime.now(tz).strftime("%b %d, %Y")

        if not observations:
            await self._slack.send_blocks([
                {"type": "header", "text": {"type": "plain_text",
                                            "text": ":eyes: Competitor Brief"}},
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    f"_No usable observations for week of {week_label}. "
                    "Check logs — likely API/search failure._"}},
            ], text=f"Competitor brief (empty) — {week_label}")
            return

        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": ":eyes: Competitor Watch"}},
            {"type": "context", "elements": [{
                "type": "mrkdwn",
                "text": f"Week of *{week_label}* — Vrai / Catbird / Mejuri / Aurate / Mateo",
            }]},
            {"type": "divider"},
        ]

        for i, obs in enumerate(observations, 1):
            who = obs.get("who", "—")
            what = obs.get("what", "")
            why = obs.get("why_matters", "")
            action = obs.get("action", "")
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{i}. {who}* — {what}\n"
                    f"_Why it matters:_ {why}\n"
                    f":point_right: *Action:* {action}"
                ),
            }})

        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": "_WebSearch-backed synthesis, not daily scraping. "
                    "Complement with Meta Ad Library browsing for raw creative inputs._",
        }]})

        await self._slack.send_blocks(blocks, text=f"Competitor brief — {week_label}")
