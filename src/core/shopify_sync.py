"""Continuous reconciliation between Shopify and Supabase.

Webhook subscriptions can miss events (Shopify delivery failure, our
service down during delivery, subscription gap at cutover). Reconciliation
crons catch up by polling Shopify for the full picture and diffing against
our mirror.

This module centralizes the logic so both the daily crons and the one-off
backfill script share the same translation + diff code.

Public API:
    await reconcile_products(db, shopify_client) -> dict of counts
    await reconcile_customers(db, shopify_client) -> dict of counts
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)


async def reconcile_products(
    db: AsyncDatabase | None = None,
    delete_missing: bool = True,
) -> dict[str, int]:
    """Mirror all Shopify products → Supabase `products`.

    Operation:
      1. Fetch every product from Shopify (paginated to 250/page).
      2. Upsert each into Supabase via the shared webhook translator.
      3. If `delete_missing=True`: find Supabase products with a
         shopify_product_id that no longer exists in Shopify; delete them.
         (Covers the case where Shopify delete webhook fired before we
         subscribed, or was never delivered.)

    Returns counts for logging / Slack: upserted, skipped, deleted, errors.
    """
    from src.api.shopify_webhooks import _shopify_product_to_supabase_row

    db = db or AsyncDatabase()
    shop = settings.shopify_shop_domain
    token = settings.shopify_access_token
    api_version = settings.shopify_api_version

    if not (shop and token):
        return {"upserted": 0, "skipped": 0, "deleted": 0, "errors": 0, "skip_reason": "shopify_not_configured"}

    # Fetch every product with pagination (Shopify uses Link headers)
    shopify_products = await _fetch_all_paginated(
        url=f"https://{shop}/admin/api/{api_version}/products.json?limit=250",
        headers={"X-Shopify-Access-Token": token},
        list_key="products",
    )

    upserted = 0
    skipped = 0
    errors = 0
    seen_shopify_ids: set[int] = set()

    for sp in shopify_products:
        shopify_id = sp.get("id")
        if shopify_id:
            seen_shopify_ids.add(int(shopify_id))
        row = _shopify_product_to_supabase_row(sp)
        if not row.get("sku"):
            skipped += 1
            continue
        try:
            await db.upsert_product(row)
            upserted += 1
        except Exception:
            logger.exception("reconcile_products: upsert failed for sku=%s", row.get("sku"))
            errors += 1

    deleted = 0
    if delete_missing and seen_shopify_ids:
        try:
            existing = await db.get_all_products()
            for row in existing or []:
                sp_id = row.get("shopify_product_id")
                if not sp_id:
                    continue  # Never synced to Shopify — leave alone
                if int(sp_id) not in seen_shopify_ids:
                    await db.delete_product_by_shopify_id(int(sp_id))
                    deleted += 1
                    logger.info(
                        "reconcile_products: removed orphan sku=%s (shopify_id=%s)",
                        row.get("sku"), sp_id,
                    )
        except Exception:
            logger.exception("reconcile_products: orphan cleanup failed")
            errors += 1

    logger.info(
        "reconcile_products: upserted=%d skipped=%d deleted=%d errors=%d (shopify total=%d)",
        upserted, skipped, deleted, errors, len(shopify_products),
    )
    return {
        "upserted": upserted,
        "skipped": skipped,
        "deleted": deleted,
        "errors": errors,
        "shopify_total": len(shopify_products),
    }


async def reconcile_customers(
    db: AsyncDatabase | None = None,
    page_limit: int = 20,  # 20 × 250 = 5,000 customers safety cap
) -> dict[str, int]:
    """Mirror all Shopify customers → Supabase `customers`.

    Unlike products, we don't delete customer rows that disappear from
    Shopify — customer deletion is GDPR territory and should be handled
    by an explicit privacy flow, not silent reconciliation.
    """
    db = db or AsyncDatabase()
    shop = settings.shopify_shop_domain
    token = settings.shopify_access_token
    api_version = settings.shopify_api_version

    if not (shop and token):
        return {"upserted": 0, "skipped": 0, "errors": 0, "skip_reason": "shopify_not_configured"}

    customers = await _fetch_all_paginated(
        url=f"https://{shop}/admin/api/{api_version}/customers.json?limit=250",
        headers={"X-Shopify-Access-Token": token},
        list_key="customers",
        page_limit=page_limit,
    )

    upserted = 0
    skipped = 0
    errors = 0

    for c in customers:
        shopify_customer_id = c.get("id")
        email = (c.get("email") or "").strip()
        if not shopify_customer_id:
            skipped += 1
            continue

        name = f"{c.get('first_name', '') or ''} {c.get('last_name', '') or ''}".strip()
        row = {
            "shopify_customer_id": shopify_customer_id,
            "email": email,
            "name": name or email,
            "phone": c.get("phone") or "",
            "accepts_marketing": bool(c.get("accepts_marketing")),
            "order_count": int(c.get("orders_count") or 0),
            "lifetime_value": float(c.get("total_spent") or 0),
        }
        try:
            await db.upsert_customer(row)
            upserted += 1
        except Exception:
            logger.exception(
                "reconcile_customers: upsert failed for shopify_id=%s",
                shopify_customer_id,
            )
            errors += 1

    logger.info(
        "reconcile_customers: upserted=%d skipped=%d errors=%d (shopify total=%d)",
        upserted, skipped, errors, len(customers),
    )
    return {
        "upserted": upserted,
        "skipped": skipped,
        "errors": errors,
        "shopify_total": len(customers),
    }


async def _fetch_all_paginated(
    url: str,
    headers: dict[str, str],
    list_key: str,
    page_limit: int = 50,
) -> list[dict[str, Any]]:
    """Walk Shopify's Link-header pagination.

    Shopify's pagination uses `Link: <...>; rel="next"` headers. Each page
    returns up to 250 items. page_limit caps total pages so a runaway
    response doesn't exhaust memory.
    """
    collected: list[dict[str, Any]] = []
    next_url: str | None = url

    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(page_limit):
            if not next_url:
                break
            resp = await client.get(next_url, headers=headers)
            if resp.status_code != 200:
                logger.error(
                    "paginated fetch failed %d: %s", resp.status_code, resp.text[:200]
                )
                break
            batch = resp.json().get(list_key, [])
            collected.extend(batch)

            # Parse Link header for rel="next"
            next_url = _parse_link_header(resp.headers.get("Link", ""), rel="next")
            if not next_url:
                break

    return collected


def _parse_link_header(header: str, rel: str = "next") -> str | None:
    """Extract URL for a specific rel from a Link header.

    Format: <https://...?page_info=xyz>; rel="next", <...>; rel="previous"
    """
    if not header:
        return None
    for chunk in header.split(","):
        parts = chunk.strip().split(";")
        if len(parts) < 2:
            continue
        url_part = parts[0].strip()
        rel_part = parts[1].strip()
        if not url_part.startswith("<") or not url_part.endswith(">"):
            continue
        if f'rel="{rel}"' not in rel_part and f"rel={rel}" not in rel_part:
            continue
        return url_part[1:-1]
    return None
