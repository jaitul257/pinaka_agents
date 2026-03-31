"""Meta Conversions API (CAPI) client for server-side purchase events.

Sends purchase conversion events to Meta for ad targeting optimization.
PII is normalized and SHA-256 hashed before sending (Meta requirement).
"""

import hashlib
import logging
import time
from typing import Any

import httpx

from src.core.settings import settings

logger = logging.getLogger(__name__)


def _normalize_and_hash(value: str) -> str:
    """Normalize PII (lowercase, strip) and SHA-256 hash for Meta."""
    normalized = value.strip().lower()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_phone(phone: str) -> str:
    """Normalize phone to digits-only before hashing. Best-effort E.164.

    Meta requires digits only (no '+') with country code prefix.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    # If no country code prefix (US numbers are 10 digits), assume US (1)
    if len(digits) == 10:
        digits = "1" + digits
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


class MetaConversionsAPI:
    """Send server-side purchase events to Meta Conversions API."""

    def __init__(self):
        self._pixel_id = settings.meta_pixel_id
        self._access_token = settings.meta_capi_access_token
        self._api_version = settings.meta_graph_api_version

    @property
    def is_configured(self) -> bool:
        return bool(self._pixel_id and self._access_token)

    async def send_purchase_event(
        self,
        order_data: dict[str, Any],
        customer_email: str = "",
        customer_phone: str = "",
        customer_first_name: str = "",
        customer_last_name: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> bool:
        """Send a Purchase conversion event to Meta.

        Returns True on success, False on failure. Never raises — Meta failures
        must not block order processing.
        """
        if not self.is_configured:
            return False

        shopify_order_id = order_data.get("id") or order_data.get("shopify_order_id")
        total = float(order_data.get("total_price", 0) or order_data.get("total", 0))
        currency = order_data.get("currency", "USD")

        # Build user_data with hashed PII
        user_data: dict[str, Any] = {}
        if customer_email:
            user_data["em"] = [_normalize_and_hash(customer_email)]
        if customer_phone:
            user_data["ph"] = [_normalize_phone(customer_phone)]
        if customer_first_name:
            user_data["fn"] = [_normalize_and_hash(customer_first_name)]
        if customer_last_name:
            user_data["ln"] = [_normalize_and_hash(customer_last_name)]
        if client_ip:
            user_data["client_ip_address"] = client_ip
        if user_agent:
            user_data["client_user_agent"] = user_agent

        # Build content_ids from line items
        line_items = order_data.get("line_items", [])
        content_ids = [str(li.get("product_id", "")) for li in line_items if li.get("product_id")]

        event = {
            "event_name": "Purchase",
            "event_time": int(time.time()),
            "event_id": str(shopify_order_id),  # Dedup with browser pixel
            "action_source": "website",
            "user_data": user_data,
            "custom_data": {
                "currency": currency,
                "value": total,
                "content_ids": content_ids,
                "content_type": "product",
            },
        }

        # Add event_source_url if we have the shop domain
        if settings.shopify_shop_domain:
            event["event_source_url"] = f"https://{settings.shopify_shop_domain}"

        url = f"https://graph.facebook.com/{self._api_version}/{self._pixel_id}/events"
        payload = {
            "data": [event],
            "access_token": self._access_token,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)

            if response.status_code == 200:
                logger.info(
                    "Meta CAPI purchase event sent for order #%s ($%.2f)",
                    shopify_order_id, total,
                )
                return True
            else:
                logger.error(
                    "Meta CAPI failed for order #%s: %d %s",
                    shopify_order_id, response.status_code, response.text[:200],
                )
                return False
        except Exception:
            logger.exception("Meta CAPI request failed for order #%s", shopify_order_id)
            return False
