"""Tiered approval policy — converts Slack-fatigue into agent ownership.

Every agent action now gets classified into one of three tiers:

    AUTO     — routine, templated, reversible. Send without asking.
               Logged to auto_sent_actions so founder can review/undo
               after the fact from the dashboard.

    REVIEW   — medium-stakes, founder-voice-sensitive. Existing Slack
               approve/reject flow stays.

    ESCALATE — money, trust, or legal risk. Always surfaces to Slack
               with extra context, and logs an observation.

Start conservative: only the most mechanical actions auto-send in v1.
Widen the AUTO set over time as trust builds (measured by flag_rate
on auto_sent_actions — if flagged <5% of the time over 30 days, good
candidate to keep or promote).
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any

from src.core.database import Database

logger = logging.getLogger(__name__)


class Tier(str, Enum):
    AUTO = "auto"
    REVIEW = "review"
    ESCALATE = "escalate"


# Conservative v1: only fully-templated actions with very low
# personalisation risk. A wrong "care guide email" doesn't cost money
# or damage trust. A wrong support reply might.
AUTO_ACTIONS: set[str] = {
    "crafting_update_email",       # templated day 2-3 update
    "lifecycle_welcome_1",         # welcome series day 0
    "lifecycle_welcome_2",         # welcome series day 3
    "lifecycle_welcome_3",         # welcome series day 7
    "lifecycle_welcome_4",         # welcome series day 14
    "lifecycle_welcome_5",         # welcome series day 21
    "care_guide_reminder",         # quarterly care tip (templated)
    "review_request_90d",          # post-delivery review ask
    "reorder_reminder_180d",       # first reorder nudge
    "reorder_reminder_365d",       # anniversary reorder
}

REVIEW_ACTIONS: set[str] = {
    "customer_response",           # support reply — voice matters
    "cart_recovery",               # abandoned cart email
    "listing_publish",             # product listing to Shopify
    "ad_creative_publish",         # new ad to Meta
    "poq_batch",                   # Piece of the Quarter batch
    "seo_blog_publish",            # new blog post
    "lifecycle_anniversary",       # first purchase anniversary — more personal
    "lifecycle_vip_nurture",       # VIP treatment email
    "lifecycle_sunset_winback",    # "we miss you" sunset email
}

ESCALATE_ACTIONS: set[str] = {
    "order_hold",                  # shipping hold
    "order_cancel",                # cancel order
    "fraud_review",                # shipment approval for high-risk
    "budget_change",               # ad budget up/down
    "refund_approval",             # refund above threshold
    "customer_exception",          # edge case handoff
}


def classify(action_type: str) -> Tier:
    """Classify an action into a tier. Unknown actions default to REVIEW
    (safest fallback — don't auto-send something we haven't thought about)."""
    if action_type in AUTO_ACTIONS:
        return Tier.AUTO
    if action_type in ESCALATE_ACTIONS:
        return Tier.ESCALATE
    return Tier.REVIEW  # default: unknown → safe side


async def log_auto_sent(
    agent_name: str,
    action_type: str,
    payload: dict[str, Any],
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> int | None:
    """Record an AUTO-tier action that bypassed Slack. Returns row id.

    The dashboard at /dashboard/agents shows these so founder can
    review and flag mistakes.
    """
    if classify(action_type) != Tier.AUTO:
        logger.warning(
            "log_auto_sent called for non-AUTO action %s (tier=%s)",
            action_type, classify(action_type).value,
        )
    try:
        db = Database()

        def _insert():
            return db._client.table("auto_sent_actions").insert({
                "agent_name": agent_name,
                "action_type": action_type,
                "entity_type": entity_type,
                "entity_id": str(entity_id) if entity_id is not None else None,
                "payload": payload,
            }).execute()

        res = await asyncio.to_thread(_insert)
        if res.data:
            return int(res.data[0]["id"])
    except Exception:
        logger.exception("auto_sent log failed for %s/%s", agent_name, action_type)
    return None


async def recent_auto_sent(
    limit: int = 50,
    agent_name: str | None = None,
    only_flagged: bool = False,
) -> list[dict[str, Any]]:
    try:
        db = Database()

        def _query():
            q = db._client.table("auto_sent_actions").select("*")
            if agent_name:
                q = q.eq("agent_name", agent_name)
            if only_flagged:
                q = q.eq("flagged", True)
            return q.order("created_at", desc=True).limit(limit).execute()

        res = await asyncio.to_thread(_query)
        return res.data or []
    except Exception:
        logger.exception("recent_auto_sent read failed")
        return []


async def flag_auto_sent(action_id: int, reason: str) -> bool:
    """Founder clicked 'this was wrong' from the dashboard."""
    try:
        db = Database()

        def _update():
            return db._client.table("auto_sent_actions").update({
                "flagged": True,
                "flag_reason": reason,
            }).eq("id", action_id).execute()

        res = await asyncio.to_thread(_update)
        return bool(res.data)
    except Exception:
        logger.exception("flag_auto_sent failed for id=%s", action_id)
        return False


async def auto_flag_rate_30d(action_type: str) -> dict[str, Any]:
    """Measure flagging rate for an action over the last 30 days.

    Returns {count, flagged, rate_pct}. Used to decide whether to
    widen or tighten AUTO.
    """
    try:
        db = Database()
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        def _query():
            return (db._client.table("auto_sent_actions")
                .select("id,flagged")
                .eq("action_type", action_type)
                .gte("created_at", since)
                .execute())

        res = await asyncio.to_thread(_query)
        rows = res.data or []
        total = len(rows)
        flagged = sum(1 for r in rows if r.get("flagged"))
        return {
            "count": total,
            "flagged": flagged,
            "rate_pct": (flagged / total * 100) if total else 0.0,
        }
    except Exception:
        logger.exception("auto_flag_rate_30d failed")
        return {"count": 0, "flagged": 0, "rate_pct": 0.0}
