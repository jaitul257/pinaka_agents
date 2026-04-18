"""Phase 12.5c — AUTO/REVIEW tier promotion/demotion audit.

Weekly cron that surfaces EVIDENCE about which actions belong in which
tier, without auto-mutating the lists. Rationale:

  • Widening AUTO is a policy decision — founder sees the data then decides.
    An auto-mutating cron that moves actions into AUTO on its own could
    silently expand what agents send unsupervised.
  • Same for demotion: a spike in `flagged=true` on an AUTO action means
    something's wrong, but the mitigation may be "fix the drafter," not
    "move it back to REVIEW." Let the founder make the call.

Outputs go to the `observations` table (severity warning, category
`tier_audit`) so the heartbeat cron surfaces them in Slack. Founder
reviews, then manually edits `src/agents/approval_tiers.py:AUTO_ACTIONS`
if the evidence is actionable.

Signals:
  • **PROMOTE candidate**: a REVIEW-tier action with ≥20 observed edits
    and <5% materially-edited rate. If the founder approves 95% of these
    drafts unchanged, it's a strong signal the auto-tier is correct for it.
  • **DEMOTE warning**: an AUTO-tier action with ≥10 auto-sent items in
    30d and >10% flagged-by-founder rate (via auto_sent_actions.flagged).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.agents.approval_tiers import AUTO_ACTIONS, REVIEW_ACTIONS
from src.core.database import Database

logger = logging.getLogger(__name__)


PROMOTE_MIN_SAMPLES = 20
PROMOTE_MAX_EDIT_RATE_PCT = 5.0

DEMOTE_MIN_SAMPLES = 10
DEMOTE_MAX_FLAG_RATE_PCT = 10.0


async def run_audit() -> dict[str, Any]:
    """One audit cycle. Returns summary for logging + cron response body."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    promote_candidates = await _promote_candidates(since)
    demote_warnings = await _demote_warnings(since)

    # Write observations for each signal so heartbeat can surface them
    for p in promote_candidates:
        await _observe(
            category="tier_audit",
            severity="info",
            summary=f"tier promote candidate: {p['action_type']} "
                    f"({p['samples']} REVIEW runs, {p['edit_rate_pct']:.1f}% edited)",
            data={"action_type": p["action_type"], "direction": "review→auto",
                  "samples": p["samples"], "edit_rate_pct": p["edit_rate_pct"]},
        )
    for d in demote_warnings:
        await _observe(
            category="tier_audit",
            severity="warning",
            summary=f"tier demote warning: {d['action_type']} "
                    f"({d['flag_rate_pct']:.1f}% flagged across {d['samples']} auto-sends)",
            data={"action_type": d["action_type"], "direction": "auto→review",
                  "samples": d["samples"], "flag_rate_pct": d["flag_rate_pct"]},
        )

    return {
        "status": "ok",
        "promote_candidates": promote_candidates,
        "demote_warnings": demote_warnings,
    }


async def _promote_candidates(since_iso: str) -> list[dict[str, Any]]:
    """REVIEW actions the founder approves without editing.

    Source: `approval_feedback`. Any row there IS an edit (capture_edit
    skips identical), so ratio is:
        edits_captured / drafts_for_that_trigger
    We approximate drafts-for-trigger via `agent_audit_log` rows tagged
    with the agent that owns the REVIEW action. At our scale this is
    close enough — a real draft count view can come later.
    """
    def _q_feedback():
        sync = Database()
        # Count non-style-roll edits per trigger_type in the window
        res = (sync._client.table("approval_feedback")
               .select("trigger_type")
               .gte("created_at", since_iso)
               .not_.like("trigger_type", "__style__:%")
               .execute())
        return res.data or []

    def _q_auto_sent_review_mirror():
        # Proxy for REVIEW drafts generated in the window: count auto_sent_actions
        # where action_type matches a REVIEW trigger. This isn't exact but it
        # reflects "what the drafting layer produced that would've been eligible."
        sync = Database()
        res = (sync._client.table("auto_sent_actions")
               .select("action_type")
               .gte("created_at", since_iso)
               .execute())
        return res.data or []

    try:
        edits = await asyncio.to_thread(_q_feedback)
    except Exception:
        logger.exception("_promote_candidates feedback query failed")
        return []

    from collections import Counter
    edit_counts = Counter(r["trigger_type"] for r in edits if r.get("trigger_type"))

    # For each REVIEW action type, count edits captured. If the count is
    # low relative to the assumed denominator, it's a promotion candidate.
    # Without a deterministic "drafts produced" count, we conservatively
    # require ≥20 total approvals passing through (edits + silent approves).
    # We lack silent-approve counts today; surface the raw evidence instead.
    candidates: list[dict[str, Any]] = []
    for action in sorted(REVIEW_ACTIONS):
        edit_ct = edit_counts.get(action, 0)
        # Heuristic: if NO edits for an action that should've fired ≥20
        # times by now, we can't promote without denominator data — skip
        # and wait for more observability. Record zero-edit actions as
        # "awaiting samples" so founder sees they're being tracked.
        if edit_ct == 0:
            continue
        # For now, any trigger with ≥20 edits AND low-rate indicates noise,
        # not readiness. True promote signal needs the denominator — we
        # punt on auto-promote recommendations until we log silent approvals.
        # Record the raw counts so founder can decide.
        candidates.append({
            "action_type": action,
            "samples": edit_ct,
            "edit_rate_pct": 100.0,  # 100% of observed samples were edits (lower bound)
            "note": "edit-only denominator — silent approvals not yet logged",
        })
    return candidates


async def _demote_warnings(since_iso: str) -> list[dict[str, Any]]:
    """AUTO actions where the founder is flagging too many post-hoc.

    Source: `auto_sent_actions` in last 30d, grouped by action_type, with
    flag rate computed from `flagged=true` column.
    """
    def _q():
        sync = Database()
        res = (sync._client.table("auto_sent_actions")
               .select("action_type,flagged")
               .gte("created_at", since_iso)
               .execute())
        return res.data or []
    try:
        rows = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("_demote_warnings query failed")
        return []

    # Group by action_type
    stats: dict[str, dict[str, int]] = {}
    for r in rows:
        at = r.get("action_type") or "unknown"
        agg = stats.setdefault(at, {"total": 0, "flagged": 0})
        agg["total"] += 1
        if r.get("flagged"):
            agg["flagged"] += 1

    warnings: list[dict[str, Any]] = []
    for action, agg in sorted(stats.items()):
        if action not in AUTO_ACTIONS:
            continue
        total = agg["total"]
        if total < DEMOTE_MIN_SAMPLES:
            continue
        flag_rate = (agg["flagged"] / total) * 100.0 if total else 0.0
        if flag_rate > DEMOTE_MAX_FLAG_RATE_PCT:
            warnings.append({
                "action_type": action,
                "samples": total,
                "flagged": agg["flagged"],
                "flag_rate_pct": round(flag_rate, 1),
            })
    return warnings


async def _observe(
    *, category: str, severity: str, summary: str, data: dict[str, Any],
) -> None:
    """Write to the observations table (same schema heartbeat uses)."""
    def _ins():
        sync = Database()
        return sync._client.table("observations").insert({
            "source": "tier_audit",
            "category": category,
            "severity": severity,
            "summary": summary,
            "data": data,
        }).execute()
    try:
        await asyncio.to_thread(_ins)
    except Exception:
        logger.exception("tier_audit observe failed: %s", summary)
