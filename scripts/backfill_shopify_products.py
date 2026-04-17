"""One-off backfill: pull every active Shopify product and upsert into Supabase.

Run this once after registering the products/* webhooks to catch up on
products that existed before webhook subscription. Idempotent — re-runs
are safe. Uses the same translator as the live webhook so rows match.

USAGE:
    railway run .venv/bin/python scripts/backfill_shopify_products.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx

from src.api.shopify_webhooks import _shopify_product_to_supabase_row
from src.core.database import AsyncDatabase


async def main() -> int:
    shop = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2025-01")

    if not shop or not token:
        print("ERROR: SHOPIFY_SHOP_DOMAIN or SHOPIFY_ACCESS_TOKEN not set.")
        return 1

    db = AsyncDatabase()

    # Fetch ALL products (both active and draft — we mirror state, not filter)
    url = f"https://{shop}/admin/api/{api_version}/products.json?limit=250"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"X-Shopify-Access-Token": token})
    resp.raise_for_status()
    products = resp.json().get("products", [])
    print(f"Pulled {len(products)} products from Shopify")

    existing_skus = {p["sku"] for p in await db.get_all_products() if p.get("sku")}
    print(f"Supabase already has {len(existing_skus)} products")

    new_count = 0
    updated_count = 0
    skipped_count = 0
    for sp in products:
        row = _shopify_product_to_supabase_row(sp)
        if not row.get("sku"):
            skipped_count += 1
            print(f"  SKIP {sp.get('title')}: no SKU on first variant")
            continue

        was_new = row["sku"] not in existing_skus
        try:
            await db.upsert_product(row)
            if was_new:
                new_count += 1
                print(f"  NEW  {row['sku']:<30} {row['name'][:50]}")
            else:
                updated_count += 1
        except Exception as e:
            print(f"  ERR  {row['sku']}: {e}")
            skipped_count += 1

    print()
    print(f"Backfill complete: {new_count} new, {updated_count} updated, {skipped_count} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
