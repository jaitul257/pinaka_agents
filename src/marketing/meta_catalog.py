"""Meta Product Catalog Batch API client for syncing product feeds.

Syncs products from Supabase to a Meta Commerce Manager catalog.
Enables Dynamic Product Ads (DPA) on Meta, which auto-show the right
jewelry to users based on browsing intent.

Uses the same system user token as CAPI (meta_capi_access_token).
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.core.settings import settings

logger = logging.getLogger(__name__)

# Meta Catalog Batch API limit
BATCH_SIZE = 5000


@dataclass
class CatalogSyncResult:
    """Summary of a catalog sync run."""
    items_synced: int = 0
    items_failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.items_synced == 0 and self.items_failed == 0:
            return "skipped"
        if self.items_failed > 0:
            return "partial_failure"
        return "ok"


def _slugify(name: str) -> str:
    """Convert product name to URL-friendly handle (Shopify convention)."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _get_retail_price(pricing: dict | None) -> str:
    """Extract the first variant's retail price from pricing JSONB.

    Returns price as string for Meta (e.g. "2850.00 USD").
    Returns empty string if pricing is missing or malformed.
    """
    if not pricing or not isinstance(pricing, dict):
        return ""

    for variant_name, variant_data in pricing.items():
        if isinstance(variant_data, dict) and "retail" in variant_data:
            price = float(variant_data["retail"])
            return f"{price:.2f} USD"

    return ""


def map_product_to_catalog_item(product: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Supabase product row to Meta Catalog item format.

    Returns None if the product is missing required fields (no price or no name).
    """
    name = product.get("name", "")
    sku = product.get("sku", "")
    if not name or not sku:
        return None

    pricing = product.get("pricing") or {}
    price_str = _get_retail_price(pricing)
    if not price_str:
        logger.warning("Product %s has no retail price, skipping catalog sync", sku)
        return None

    # Build product URL from storefront domain (custom domain preferred) + slugified name
    shop_domain = settings.storefront_domain
    handle = _slugify(name)
    link = f"https://{shop_domain}/products/{handle}" if shop_domain else ""

    # Images: use first image, or empty string
    images = product.get("images") or []
    image_link = images[0] if images else ""

    # Materials for description enrichment
    materials = product.get("materials") or {}
    metal = materials.get("metal", "")
    total_carat = materials.get("total_carat", 0)

    # Build description from story + materials
    story = product.get("story", "")
    description_parts = []
    if story:
        description_parts.append(story)
    if metal:
        description_parts.append(f"Crafted in {metal}.")
    if total_carat:
        description_parts.append(f"{total_carat} total carat weight.")
    description = " ".join(description_parts) or name

    category = product.get("category", "")

    return {
        "method": "UPDATE",
        "retailer_id": sku,
        "data": {
            "name": name,
            "description": description,
            "availability": "in stock",
            "condition": "new",
            "price": price_str,
            "link": link,
            "image_link": image_link,
            "brand": "Pinaka Jewellery",
            "google_product_category": "Apparel & Accessories > Jewelry",
            "custom_label_0": category,
        },
    }


class MetaCatalogSync:
    """Sync products from Supabase to a Meta Commerce Manager catalog.

    Uses the Catalog Batch API to create/update items in bulk.
    """

    def __init__(self):
        self._access_token = settings.meta_capi_access_token
        self._catalog_id = settings.meta_catalog_id
        self._api_version = settings.meta_graph_api_version

    @property
    def is_configured(self) -> bool:
        return bool(self._access_token and self._catalog_id)

    async def sync_products(self, products: list[dict[str, Any]]) -> CatalogSyncResult:
        """Sync a list of Supabase product rows to Meta catalog.

        Maps each product to Meta format, batches in groups of 5000,
        and POSTs to the Catalog Batch API.
        """
        result = CatalogSyncResult()

        if not self.is_configured:
            result.errors.append("Meta Catalog not configured (missing token or catalog_id)")
            return result

        if not products:
            logger.info("No products to sync to Meta catalog")
            return result

        # Map products to catalog items, filtering out invalid ones
        items = []
        for product in products:
            item = map_product_to_catalog_item(product)
            if item:
                items.append(item)
            else:
                result.items_failed += 1

        if not items:
            logger.warning("All products failed mapping, nothing to sync")
            return result

        # Send in batches of BATCH_SIZE
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i : i + BATCH_SIZE]
            batch_result = await self._send_batch(batch)
            result.items_synced += batch_result["synced"]
            result.items_failed += batch_result["failed"]
            if batch_result.get("error"):
                result.errors.append(batch_result["error"])

        logger.info(
            "Meta catalog sync: %d synced, %d failed",
            result.items_synced, result.items_failed,
        )
        return result

    async def _send_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """POST a batch of items to Meta Catalog Batch API."""
        import json

        url = (
            f"https://graph.facebook.com/{self._api_version}"
            f"/{self._catalog_id}/items_batch"
        )
        payload = {
            "access_token": self._access_token,
            "requests": json.dumps(items),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, data=payload)
        except Exception as e:
            logger.exception("Meta catalog batch request failed")
            return {"synced": 0, "failed": len(items), "error": str(e)}

        if response.status_code != 200:
            error_msg = f"Meta catalog batch API error {response.status_code}: {response.text[:300]}"
            logger.error(error_msg)
            return {"synced": 0, "failed": len(items), "error": error_msg}

        body = response.json()
        # Meta returns handles array for successful items
        handles = body.get("handles", [])
        synced = len(handles)
        failed = len(items) - synced

        # Check for validation_status errors
        validation_status = body.get("validation_status", [])
        for vs in validation_status:
            if vs.get("errors"):
                for err in vs["errors"]:
                    logger.warning(
                        "Meta catalog item error (retailer_id=%s): %s",
                        vs.get("retailer_id", "?"), err.get("message", "unknown"),
                    )

        return {"synced": synced, "failed": failed}
