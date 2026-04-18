"""Google Merchant Center Content API client for syncing product feeds.

Syncs products from Supabase to Google Merchant Center for Shopping ads.
Uses a service account for authentication (no OAuth user consent needed).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class MerchantSyncResult:
    """Summary of a Merchant Center sync run."""
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
    """Convert product name to URL-friendly handle."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _get_retail_price(
    pricing: dict | None,
    variant_options: list | None = None,
) -> float | None:
    """Return the smallest retail price we can find.

    Tries in order:
      1. pricing.{variant}.retail (legacy shape from manually-added products)
      2. variant_options[].price (current Shopify-webhook-synced shape)

    Returns the MIN price across variants — GMC expects a single "from"
    price per product, and the smallest size variant is what the customer
    starts seeing on a Shopping ad.
    """
    if pricing and isinstance(pricing, dict):
        for variant_data in pricing.values():
            if isinstance(variant_data, dict) and "retail" in variant_data:
                return float(variant_data["retail"])
    if variant_options and isinstance(variant_options, list):
        prices: list[float] = []
        for v in variant_options:
            if isinstance(v, dict):
                p = v.get("price")
                try:
                    if p is not None:
                        prices.append(float(p))
                except (ValueError, TypeError):
                    continue
        if prices:
            return min(prices)
    return None


def map_product_to_merchant_item(product: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Supabase product row to Google Merchant Center product format.

    Returns None if the product is missing required fields.
    """
    name = product.get("name", "")
    sku = product.get("sku", "")
    if not name or not sku:
        return None

    pricing = product.get("pricing") or {}
    variant_options = product.get("variant_options")
    retail_price = _get_retail_price(pricing, variant_options=variant_options)
    if retail_price is None:
        logger.warning("Product %s has no retail price, skipping merchant sync", sku)
        return None

    shop_domain = settings.storefront_domain
    handle = _slugify(name)
    link = f"https://{shop_domain}/products/{handle}" if shop_domain else ""

    images = product.get("images") or []
    image_link = images[0] if images else ""

    story = product.get("story", "")
    materials = product.get("materials") or {}
    metal = materials.get("metal", "")
    description_parts = []
    if story:
        description_parts.append(story)
    if metal:
        description_parts.append(f"Crafted in {metal}.")
    description = " ".join(description_parts) or name

    category = product.get("category", "")

    return {
        "offerId": sku,
        "title": name,
        "description": description,
        "link": link,
        "imageLink": image_link,
        "contentLanguage": "en",
        "targetCountry": "US",
        "channel": "online",
        "availability": "in stock",
        "condition": "new",
        "price": {"value": str(retail_price), "currency": "USD"},
        "brand": "Pinaka Jewellery",
        "googleProductCategory": "Apparel & Accessories > Jewelry",
        # Content API v2.1 expects `productTypes` (array, plural), not
        # `productType`. Previously rejected every batch with
        # "Unknown name productType". Multiple entries are allowed — we
        # send our single category in a one-element list.
        "productTypes": [category] if category else [],
        "shipping": [{
            "country": "US",
            "service": "Standard",
            "price": {"value": "0", "currency": "USD"},
        }],
    }


class GoogleMerchantSync:
    """Sync products from Supabase to Google Merchant Center via Content API."""

    def __init__(self):
        self._merchant_id = settings.google_merchant_id
        self._service = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self._merchant_id
            and (settings.google_service_account_path or settings.google_service_account_json)
        )

    def _get_service(self):
        """Lazy-init the Merchant Center Content API service."""
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            if settings.google_service_account_json:
                import io
                info = json.loads(settings.google_service_account_json)
                credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=["https://www.googleapis.com/auth/content"]
                )
            else:
                credentials = service_account.Credentials.from_service_account_file(
                    settings.google_service_account_path,
                    scopes=["https://www.googleapis.com/auth/content"],
                )

            self._service = build("content", "v2.1", credentials=credentials)
        return self._service

    async def sync_products(self, products: list[dict[str, Any]]) -> MerchantSyncResult:
        """Sync a list of Supabase product rows to Google Merchant Center.

        Maps each product to Merchant format and uses custombatch for bulk insert/update.
        """
        import asyncio

        result = MerchantSyncResult()

        if not self.is_configured:
            result.errors.append("Google Merchant not configured")
            return result

        if not products:
            logger.info("No products to sync to Google Merchant Center")
            return result

        # Map products to Merchant items
        items = []
        for product in products:
            item = map_product_to_merchant_item(product)
            if item:
                items.append(item)
            else:
                result.items_failed += 1

        if not items:
            logger.warning("All products failed mapping, nothing to sync")
            return result

        # Build custombatch request
        batch_entries = []
        for i, item in enumerate(items):
            batch_entries.append({
                "batchId": i,
                "merchantId": self._merchant_id,
                "method": "insert",
                "product": item,
            })

        try:
            batch_result = await asyncio.to_thread(
                self._execute_batch, {"entries": batch_entries}
            )
        except Exception as e:
            logger.exception("Google Merchant batch request failed")
            result.items_failed += len(items)
            result.errors.append(str(e))
            return result

        # Process batch response
        for entry in batch_result.get("entries", []):
            if entry.get("errors"):
                result.items_failed += 1
                for error in entry["errors"].get("errors", []):
                    msg = error.get("message", "unknown error")
                    logger.warning(
                        "Merchant item error (batch %s): %s",
                        entry.get("batchId"), msg,
                    )
                    if msg not in result.errors:
                        result.errors.append(msg)
            else:
                result.items_synced += 1

        logger.info(
            "Google Merchant sync: %d synced, %d failed",
            result.items_synced, result.items_failed,
        )
        return result

    def _execute_batch(self, body: dict) -> dict:
        """Execute custombatch request (sync, called from thread)."""
        service = self._get_service()
        return service.products().custombatch(body=body).execute()


class GoogleMerchantError(Exception):
    """Raised when Google Merchant API calls fail."""
    pass
