"""Approval feedback loop — Phase 12.5.

When the founder edits a Claude draft before approving, we capture the diff.
Each trigger type (customer_response, lifecycle_anniversary, etc.) builds a
corpus of {original → edited} pairs.

Once per trigger hits 10+ entries, the Sunday 11 PM ET cron asks Claude to
summarize the editing pattern and stores the result in a `founder_style`
record. The ContextAssembler then injects this guidance into future prompts
for that trigger.

Agents get smarter about the founder's voice without anyone having to write
prompt tweaks by hand.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from anthropic import Anthropic

from src.core.database import Database
from src.core.settings import settings

logger = logging.getLogger(__name__)


MIN_EDITS_FOR_STYLE = 10


async def capture_edit(
    agent_name: str,
    trigger_type: str,
    original_text: str,
    edited_text: str,
    context: dict[str, Any] | None = None,
) -> int | None:
    """Store a founder edit for later style learning.

    Skip if the texts are identical — no signal there.
    """
    if original_text.strip() == edited_text.strip():
        return None
    try:
        def _insert():
            sync = Database()
            return sync._client.table("approval_feedback").insert({
                "agent_name": agent_name,
                "trigger_type": trigger_type,
                "original_text": original_text,
                "edited_text": edited_text,
                "context": context or {},
            }).execute()
        res = await asyncio.to_thread(_insert)
        if res.data:
            return int(res.data[0]["id"])
    except Exception:
        logger.exception("capture_edit failed for %s/%s", agent_name, trigger_type)
    return None


async def roll_founder_style() -> dict[str, Any]:
    """For every (agent, trigger) with >=10 un-incorporated edits,
    summarize editing pattern via Claude into a style rule.

    Writes to founder_style table (upsert per trigger) and marks the
    approval_feedback rows as incorporated=true.
    """
    clusters = await _cluster_pending_edits()

    rolled: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for (agent_name, trigger_type), rows in clusters.items():
        if len(rows) < MIN_EDITS_FOR_STYLE:
            skipped.append({"agent": agent_name, "trigger": trigger_type, "count": len(rows)})
            continue

        summary = await _summarize_edits(agent_name, trigger_type, rows)
        if not summary:
            continue

        await _upsert_style(agent_name, trigger_type, summary, sample_count=len(rows))

        ids = [r["id"] for r in rows]
        await _mark_incorporated(ids)

        rolled.append({
            "agent": agent_name,
            "trigger": trigger_type,
            "sample_count": len(rows),
            "summary_snippet": summary[:120],
        })

    return {"rolled": rolled, "skipped_below_threshold": skipped}


async def _cluster_pending_edits() -> dict[tuple[str, str], list[dict[str, Any]]]:
    def _q():
        sync = Database()
        return (sync._client.table("approval_feedback")
                .select("id,agent_name,trigger_type,original_text,edited_text,context")
                .eq("incorporated", False)
                .order("created_at")
                .execute())
    try:
        res = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("cluster_pending_edits query failed")
        return {}

    clusters: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in res.data or []:
        key = (r["agent_name"], r["trigger_type"])
        clusters.setdefault(key, []).append(r)
    return clusters


async def _summarize_edits(
    agent_name: str, trigger_type: str, rows: list[dict[str, Any]],
) -> str | None:
    if not settings.anthropic_api_key:
        return None

    pairs = []
    for r in rows[:20]:  # cap at 20 pairs to fit context
        pairs.append(
            f"--- Example ---\nDRAFT: {r['original_text']}\nFINAL: {r['edited_text']}"
        )
    corpus = "\n\n".join(pairs)

    prompt = f"""You are learning the founder Jaitul's personal voice for Pinaka Jewellery (premium handcrafted diamond tennis bracelets).

Context: You drafted {len(rows)} {trigger_type} messages. Jaitul edited each before sending. Study the DRAFT → FINAL diffs and extract the consistent stylistic rules he applies.

{corpus}

Write 4-7 bullet rules describing exactly how Jaitul prefers messages in this category to be written. Be concrete — name actual phrasings, tone choices, what he deletes, what he adds. No generic advice ("be warm") — say the specific thing ("drops em-dashes, replaces with periods" or "always mentions the 15-business-day window"). Output bullets only, no preamble."""

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        def _call():
            return client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
        resp = await asyncio.to_thread(_call)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception:
        logger.exception("summarize_edits claude call failed for %s/%s",
                         agent_name, trigger_type)
        return None


async def _upsert_style(
    agent_name: str, trigger_type: str, summary: str, sample_count: int,
) -> None:
    """Store as a row in approval_feedback with a special marker, OR
    a dedicated founder_style table. For simplicity, we reuse
    approval_feedback with trigger_type=__style__:{original}."""
    def _up():
        sync = Database()
        return sync._client.table("approval_feedback").upsert({
            "agent_name": agent_name,
            "trigger_type": f"__style__:{trigger_type}",
            "original_text": "",
            "edited_text": summary,
            "context": {"sample_count": sample_count,
                        "rolled_at": datetime.now(timezone.utc).isoformat()},
            "incorporated": True,
        }, on_conflict="agent_name,trigger_type").execute()
    try:
        await asyncio.to_thread(_up)
    except Exception:
        # Fallback: plain insert (upsert may fail if unique constraint missing)
        def _ins():
            sync = Database()
            return sync._client.table("approval_feedback").insert({
                "agent_name": agent_name,
                "trigger_type": f"__style__:{trigger_type}",
                "original_text": "",
                "edited_text": summary,
                "context": {"sample_count": sample_count,
                            "rolled_at": datetime.now(timezone.utc).isoformat()},
                "incorporated": True,
            }).execute()
        try:
            await asyncio.to_thread(_ins)
        except Exception:
            logger.exception("_upsert_style failed for %s/%s", agent_name, trigger_type)


async def _mark_incorporated(ids: list[int]) -> None:
    if not ids:
        return
    def _up():
        sync = Database()
        return (sync._client.table("approval_feedback")
                .update({"incorporated": True})
                .in_("id", ids)
                .execute())
    try:
        await asyncio.to_thread(_up)
    except Exception:
        logger.exception("mark_incorporated failed for %d ids", len(ids))


async def founder_style_for(agent_name: str, trigger_type: str) -> str | None:
    """Fetch the latest rolled style guidance for a trigger. Used by
    ContextAssembler to inject into prompts."""
    def _q():
        sync = Database()
        return (sync._client.table("approval_feedback")
                .select("edited_text,context")
                .eq("agent_name", agent_name)
                .eq("trigger_type", f"__style__:{trigger_type}")
                .order("created_at", desc=True)
                .limit(1)
                .execute())
    try:
        res = await asyncio.to_thread(_q)
        rows = res.data or []
        if rows:
            return rows[0].get("edited_text")
    except Exception:
        logger.exception("founder_style_for read failed")
    return None


async def all_styles() -> list[dict[str, Any]]:
    """All rolled style rules — for the dashboard display."""
    def _q():
        sync = Database()
        return (sync._client.table("approval_feedback")
                .select("agent_name,trigger_type,edited_text,context,created_at")
                .like("trigger_type", "__style__:%")
                .order("created_at", desc=True)
                .execute())
    try:
        res = await asyncio.to_thread(_q)
        return res.data or []
    except Exception:
        logger.exception("all_styles read failed")
        return []
