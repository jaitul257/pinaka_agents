"""Meta → Supabase ad status reverse-sync (Phase 12 gap-close).

When the founder pauses or resumes an ad directly in Meta Ads Manager
(instead of through our dashboard), our `ad_creatives.status` drifts
from reality. Fatigue detection, creative rotation, and the dashboard
brief all start reasoning against stale state.

This module bulk-fetches the current Meta status for every ad_creative
that has a `meta_ad_id` or `meta_creative_id`, and updates our DB to
match.

Mapping (Meta → Pinaka):
  ACTIVE            → live
  PAUSED            → paused
  DELETED/ARCHIVED  → paused (we don't have a separate archived state)
  PENDING_REVIEW    → unchanged (Meta's own review)
  DISAPPROVED       → paused

Ad-level status wins if present; falls back to creative-level status
for drafts that haven't been attached to an ad yet.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)


META_STATUS_TO_OURS: dict[str, str] = {
    "ACTIVE": "live",
    "PAUSED": "paused",
    "DELETED": "paused",
    "ARCHIVED": "paused",
    "DISAPPROVED": "paused",
    # Leave unchanged for Meta's review states — our "published" state already covers this.
    "PENDING_REVIEW": "",
    "IN_PROCESS": "",
    "WITH_ISSUES": "",
}


async def reconcile_ad_statuses(db: AsyncDatabase | None = None) -> dict[str, int]:
    """Pull current ad/creative status from Meta for all our live or published
    creatives, update DB rows whose state has drifted.

    Returns counts for logging: checked, updated, missing_from_meta, errors.
    """
    db = db or AsyncDatabase()

    if not (settings.meta_ads_access_token and settings.meta_ad_account_id):
        return {"skip_reason": "meta_not_configured", "checked": 0, "updated": 0, "missing_from_meta": 0, "errors": 0}

    # Only rows that have actually been pushed to Meta. No point GETting for drafts.
    client = db._sync._client
    import asyncio
    resp = await asyncio.to_thread(
        lambda: (
            client.table("ad_creatives")
            .select("id,sku,status,meta_creative_id,meta_ad_id,variant_label")
            .in_("status", ["published", "live", "paused", "publishing"])
            .neq("meta_creative_id", None)
            .execute()
        )
    )
    rows = resp.data or []
    if not rows:
        return {"checked": 0, "updated": 0, "missing_from_meta": 0, "errors": 0}

    # Bulk-fetch ad statuses (prefer ad_id since it's the object serving impressions).
    # Meta lets you GET /v25.0/?ids=ID1,ID2 up to ~50 ids per call.
    ad_ids = [r["meta_ad_id"] for r in rows if r.get("meta_ad_id")]
    creative_ids = [r["meta_creative_id"] for r in rows if r.get("meta_creative_id") and not r.get("meta_ad_id")]

    ad_status_map = await _bulk_get_status(ad_ids) if ad_ids else {}
    creative_status_map = await _bulk_get_status(creative_ids) if creative_ids else {}

    checked = 0
    updated = 0
    missing = 0
    errors = 0

    for row in rows:
        checked += 1
        ad_id = row.get("meta_ad_id")
        creative_id = row.get("meta_creative_id")
        meta_status: str | None = None

        if ad_id and ad_id in ad_status_map:
            meta_status = ad_status_map[ad_id]
        elif creative_id and creative_id in creative_status_map:
            meta_status = creative_status_map[creative_id]
        elif ad_id or creative_id:
            # We expected a status but Meta didn't return one → the object was deleted
            missing += 1
            new_status = "paused"
            if row.get("status") != new_status:
                try:
                    await _update_status(db, row["id"], new_status)
                    updated += 1
                except Exception:
                    logger.exception("ad_sync: failed to mark missing ad_creative %s as paused", row["id"])
                    errors += 1
            continue

        if not meta_status:
            continue

        new_status = META_STATUS_TO_OURS.get(meta_status.upper(), "")
        if not new_status:
            continue  # Meta review state — don't overwrite our pipeline state
        if new_status == row.get("status"):
            continue  # Already aligned

        try:
            await _update_status(db, row["id"], new_status)
            updated += 1
            logger.info(
                "ad_sync: creative #%s (%s %s) %s → %s (Meta said %s)",
                row["id"], row.get("sku"), row.get("variant_label"),
                row.get("status"), new_status, meta_status,
            )
        except Exception:
            logger.exception("ad_sync: failed to update creative #%s", row["id"])
            errors += 1

    return {
        "checked": checked,
        "updated": updated,
        "missing_from_meta": missing,
        "errors": errors,
    }


async def _bulk_get_status(ids: list[str]) -> dict[str, str]:
    """GET statuses for up to 50 Meta object IDs in one call. Chunks if needed.

    Returns {id: effective_status}. Missing IDs simply don't appear in the map
    (lets caller distinguish "deleted from Meta" from "just paused").
    """
    out: dict[str, str] = {}
    if not ids:
        return out

    unique = list(dict.fromkeys(ids))  # dedupe preserving order
    for i in range(0, len(unique), 50):
        chunk = unique[i : i + 50]
        url = f"https://graph.facebook.com/{settings.meta_graph_api_version}"
        params = {
            "ids": ",".join(chunk),
            "fields": "status,effective_status",
            "access_token": settings.meta_ads_access_token,
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, params=params)
        except Exception:
            logger.exception("ad_sync: bulk fetch network error for chunk of %d ids", len(chunk))
            continue

        if resp.status_code != 200:
            logger.warning(
                "ad_sync: bulk fetch returned %d: %s",
                resp.status_code, resp.text[:200],
            )
            continue

        body = resp.json() or {}
        for meta_id, payload in body.items():
            if not isinstance(payload, dict):
                continue
            # Prefer effective_status; fall back to status
            status = payload.get("effective_status") or payload.get("status")
            if status:
                out[str(meta_id)] = str(status)

    return out


async def _update_status(db: AsyncDatabase, creative_id: int, new_status: str) -> None:
    client = db._sync._client
    import asyncio
    await asyncio.to_thread(
        lambda: (
            client.table("ad_creatives")
            .update({"status": new_status})
            .eq("id", creative_id)
            .execute()
        )
    )
