"""Meta Ad Set optimization inspector — Purchase → Add-to-Cart + 28d click.

WHY: At 1-2 orders/week we'll never exit Meta's Purchase learning phase
(needs 50 conversions/week). Switching to ADD_TO_CART optimization gives the
algorithm 10-20x more signal at comparable intent, and the 28-day click
window matches our actual 14-45-day consideration cycle (vs Meta's default
7-day click which hides most conversions for $5K jewelry).

MIGRATION STATUS (2026-04-16): Meta blocks BOTH `optimization_goal` and
`attribution_spec` edits on published ad sets. Errors:
    "Can't Make Edits to Published Ad Set"
    "Attribution Window Update Is No Longer Supported"
The only path to change optimization is creating a NEW ad set and pausing
the old one. Since a replacement ad set doubles effective budget during
overlap, that's a deliberate budget decision — not something this script
does automatically.

USAGE (safe — dry-run mode, read-only):
    .venv/bin/python scripts/meta_ads_optimize_for_atc.py --dry-run

--apply is retained for historical reference but will fail on any
published ad set. Use `scripts/meta_create_atc_adset.py` (to be added)
when ready to cut over.
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx

from src.core.settings import settings
from src.marketing.meta_creative import _extract_meta_error


TARGET_OPTIMIZATION = "OFFSITE_CONVERSIONS"  # Conversion goal (unchanged)
TARGET_EVENT = "ADD_TO_CART"                  # was PURCHASE → now ATC
TARGET_BILLING = "IMPRESSIONS"                # Standard for conversion campaigns
TARGET_ATTRIBUTION = [
    {"event_type": "CLICK_THROUGH", "window_days": 28},
    {"event_type": "VIEW_THROUGH", "window_days": 1},
]


async def fetch_adset(adset_id: str) -> dict:
    """GET current Ad Set config. Shows us what we're about to change."""
    url = f"https://graph.facebook.com/{settings.meta_graph_api_version}/{adset_id}"
    fields = (
        "id,name,status,effective_status,optimization_goal,billing_event,"
        "promoted_object,attribution_spec,daily_budget,targeting,campaign_id"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params={
            "fields": fields,
            "access_token": settings.meta_ads_access_token,
        })
    if resp.status_code != 200:
        raise RuntimeError(f"GET adset failed: {_extract_meta_error(resp.json())}")
    return resp.json()


async def update_adset(adset_id: str, payload: dict) -> dict:
    """POST updated config to Meta Ad Set."""
    url = f"https://graph.facebook.com/{settings.meta_graph_api_version}/{adset_id}"
    body = {**payload, "access_token": settings.meta_ads_access_token}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, data=body)
    if resp.status_code != 200:
        raise RuntimeError(f"POST adset update failed: {_extract_meta_error(resp.json())}")
    return resp.json()


async def main(apply: bool) -> int:
    adset_id = settings.meta_default_adset_id
    pixel_id = settings.meta_pixel_id
    if not adset_id:
        print("ERROR: META_DEFAULT_ADSET_ID not set on Railway")
        return 1
    if not pixel_id:
        print("ERROR: META_PIXEL_ID not set")
        return 1
    if not settings.meta_ads_access_token:
        print("ERROR: META_ADS_ACCESS_TOKEN not set")
        return 1

    print(f"Ad Set: {adset_id}")
    print(f"Pixel:  {pixel_id}")
    print(f"API:    {settings.meta_graph_api_version}")
    print()

    current = await fetch_adset(adset_id)
    print("── Current config ──")
    print(json.dumps({
        "name": current.get("name"),
        "status": current.get("status"),
        "optimization_goal": current.get("optimization_goal"),
        "billing_event": current.get("billing_event"),
        "promoted_object": current.get("promoted_object"),
        "attribution_spec": current.get("attribution_spec"),
    }, indent=2))
    print()

    target_payload = {
        "optimization_goal": TARGET_OPTIMIZATION,
        "billing_event": TARGET_BILLING,
        "promoted_object": json.dumps({
            "pixel_id": pixel_id,
            "custom_event_type": TARGET_EVENT,
        }),
        # NOTE: Meta locks `attribution_spec` after ad set creation (2024+).
        # Changing attribution requires a new ad set. We attempt it but fall
        # back gracefully if Meta rejects.
        "attribution_spec": json.dumps(TARGET_ATTRIBUTION),
    }

    print("── Target config ──")
    print(f"optimization_goal: {TARGET_OPTIMIZATION}")
    print(f"billing_event:     {TARGET_BILLING}")
    print(f"custom_event_type: {TARGET_EVENT}  (was PURCHASE — too little signal at our volume)")
    print(f"attribution:       28-day click + 1-day view  (best-effort; Meta may lock after creation)")
    print()

    if not apply:
        print("Dry run. Re-run with --apply to write changes.")
        return 0

    try:
        result = await update_adset(adset_id, target_payload)
    except RuntimeError as e:
        if "Attribution Window" in str(e):
            print(f"NOTE: Meta rejected attribution_spec change: {e}")
            print("Falling back to optimization-only update (attribution is locked on existing ad sets)...")
            print()
            target_payload.pop("attribution_spec", None)
            result = await update_adset(adset_id, target_payload)
        else:
            raise
    print("── Response ──")
    print(json.dumps(result, indent=2))
    print()

    verify = await fetch_adset(adset_id)
    print("── Verified new state ──")
    print(json.dumps({
        "optimization_goal": verify.get("optimization_goal"),
        "billing_event": verify.get("billing_event"),
        "promoted_object": verify.get("promoted_object"),
        "attribution_spec": verify.get("attribution_spec"),
    }, indent=2))

    actual_event = (verify.get("promoted_object") or {}).get("custom_event_type", "")
    if actual_event != TARGET_EVENT:
        print(f"\nWARNING: custom_event_type is {actual_event!r}, expected {TARGET_EVENT!r}")
        return 2
    print("\nAd Set switched to ATC optimization. Algorithm now gets ~10-20x more signal.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without applying")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run
    sys.exit(asyncio.run(main(apply=apply)))
