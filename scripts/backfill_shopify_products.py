"""One-off wrapper around src/core/shopify_sync.reconcile_products().

For normal operation, the /cron/reconcile-products endpoint runs daily
at 6:30 AM ET. This script exists for manual runs during onboarding or
after a long outage. Use when you want to sync NOW without waiting for
the cron.

USAGE:
    railway run .venv/bin/python scripts/backfill_shopify_products.py
    railway run .venv/bin/python scripts/backfill_shopify_products.py --no-delete

--no-delete preserves Supabase rows even if they're gone from Shopify.
Default is to delete orphans (matches the cron behavior).
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.shopify_sync import reconcile_products


async def main(delete_missing: bool) -> int:
    result = await reconcile_products(delete_missing=delete_missing)
    if result.get("skip_reason"):
        print(f"SKIPPED: {result['skip_reason']}")
        return 1

    print(
        f"Reconciled {result['shopify_total']} Shopify products:\n"
        f"  upserted: {result['upserted']}\n"
        f"  skipped:  {result['skipped']} (no SKU on first variant)\n"
        f"  deleted:  {result['deleted']} (orphan Supabase rows)\n"
        f"  errors:   {result['errors']}"
    )
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-delete", action="store_true",
        help="Preserve Supabase rows even when missing from Shopify",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(delete_missing=not args.no_delete)))
