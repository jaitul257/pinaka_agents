"""Meta Conversions API (CAPI) client for server-side conversion events.

Sends conversion events (ViewContent, AddToCart, InitiateCheckout, Purchase)
to Meta for ad targeting optimization. PII is normalized and SHA-256 hashed
before sending (Meta requirement).

Dedup: every event ships with an event_id. When the browser pixel also fires
(same event_id), Meta deduplicates server + browser events. Our ATC/IC events
should mirror pixel events the client already sends via Shopify's native Meta
integration — the server-side fire is the signal backstop.
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


def _build_user_data(
    email: str = "",
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    client_ip: str = "",
    user_agent: str = "",
    fbp: str = "",
    fbc: str = "",
) -> dict[str, Any]:
    """Compose the user_data block Meta expects. Empty values are skipped."""
    user_data: dict[str, Any] = {}
    if email:
        user_data["em"] = [_normalize_and_hash(email)]
    if phone:
        user_data["ph"] = [_normalize_phone(phone)]
    if first_name:
        user_data["fn"] = [_normalize_and_hash(first_name)]
    if last_name:
        user_data["ln"] = [_normalize_and_hash(last_name)]
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if user_agent:
        user_data["client_user_agent"] = user_agent
    # Facebook browser ID (cookie `_fbp`) and click ID (cookie `_fbc`) — huge lift
    # for browser-server dedup + view-through attribution when captured.
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc
    return user_data


class MetaConversionsAPI:
    """Send server-side conversion events to Meta Conversions API."""

    def __init__(self):
        self._pixel_id = settings.meta_pixel_id
        self._access_token = settings.meta_capi_access_token
        self._api_version = settings.meta_graph_api_version

    @property
    def is_configured(self) -> bool:
        return bool(self._pixel_id and self._access_token)

    async def _post_event(self, event: dict[str, Any]) -> bool:
        """Low-level POST to /events. Never raises; returns True on 200."""
        if not self.is_configured:
            return False
        url = f"https://graph.facebook.com/{self._api_version}/{self._pixel_id}/events"
        payload = {"data": [event], "access_token": self._access_token}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
            if response.status_code == 200:
                logger.info("Meta CAPI %s event sent (id=%s)", event.get("event_name"), event.get("event_id"))
                return True
            logger.error(
                "Meta CAPI %s failed: %d %s",
                event.get("event_name"), response.status_code, response.text[:200],
            )
            return False
        except Exception:
            logger.exception("Meta CAPI request failed for %s", event.get("event_name"))
            return False

    async def send_view_content(
        self,
        product_id: str,
        value: float,
        event_id: str,
        currency: str = "USD",
        customer_email: str = "",
        client_ip: str = "",
        user_agent: str = "",
        fbp: str = "",
        fbc: str = "",
        source_url: str = "",
    ) -> bool:
        """Fire a ViewContent event (product page view)."""
        event = {
            "event_name": "ViewContent",
            "event_time": int(time.time()),
            "event_id": event_id,
            "action_source": "website",
            "user_data": _build_user_data(
                email=customer_email, client_ip=client_ip, user_agent=user_agent, fbp=fbp, fbc=fbc,
            ),
            "custom_data": {
                "currency": currency,
                "value": value,
                "content_ids": [str(product_id)] if product_id else [],
                "content_type": "product",
            },
        }
        if source_url:
            event["event_source_url"] = source_url
        elif settings.shopify_shop_domain:
            event["event_source_url"] = f"https://{settings.shopify_shop_domain}"
        return await self._post_event(event)

    async def send_add_to_cart(
        self,
        product_id: str,
        value: float,
        event_id: str,
        quantity: int = 1,
        currency: str = "USD",
        customer_email: str = "",
        client_ip: str = "",
        user_agent: str = "",
        fbp: str = "",
        fbc: str = "",
        source_url: str = "",
    ) -> bool:
        """Fire an AddToCart event. This is now our OPTIMIZATION goal on Meta Ad Sets."""
        event = {
            "event_name": "AddToCart",
            "event_time": int(time.time()),
            "event_id": event_id,
            "action_source": "website",
            "user_data": _build_user_data(
                email=customer_email, client_ip=client_ip, user_agent=user_agent, fbp=fbp, fbc=fbc,
            ),
            "custom_data": {
                "currency": currency,
                "value": value,
                "content_ids": [str(product_id)] if product_id else [],
                "content_type": "product",
                "num_items": quantity,
            },
        }
        if source_url:
            event["event_source_url"] = source_url
        return await self._post_event(event)

    async def send_initiate_checkout(
        self,
        content_ids: list[str],
        value: float,
        event_id: str,
        currency: str = "USD",
        customer_email: str = "",
        client_ip: str = "",
        user_agent: str = "",
        fbp: str = "",
        fbc: str = "",
        source_url: str = "",
    ) -> bool:
        """Fire an InitiateCheckout event (checkout started)."""
        event = {
            "event_name": "InitiateCheckout",
            "event_time": int(time.time()),
            "event_id": event_id,
            "action_source": "website",
            "user_data": _build_user_data(
                email=customer_email, client_ip=client_ip, user_agent=user_agent, fbp=fbp, fbc=fbc,
            ),
            "custom_data": {
                "currency": currency,
                "value": value,
                "content_ids": [str(cid) for cid in content_ids],
                "content_type": "product",
                "num_items": len(content_ids),
            },
        }
        if source_url:
            event["event_source_url"] = source_url
        return await self._post_event(event)

    async def send_purchase_event(
        self,
        order_data: dict[str, Any],
        customer_email: str = "",
        customer_phone: str = "",
        customer_first_name: str = "",
        customer_last_name: str = "",
        client_ip: str = "",
        user_agent: str = "",
        fbp: str = "",
        fbc: str = "",
    ) -> bool:
        """Send a Purchase conversion event to Meta.

        Returns True on success, False on failure. Never raises — Meta failures
        must not block order processing. fbp/fbc come from browser cookies if
        available (lifts match quality 10-30%).
        """
        if not self.is_configured:
            return False

        shopify_order_id = order_data.get("id") or order_data.get("shopify_order_id")
        total = float(order_data.get("total_price", 0) or order_data.get("total", 0))
        currency = order_data.get("currency", "USD")

        line_items = order_data.get("line_items", [])
        content_ids = [str(li.get("product_id", "")) for li in line_items if li.get("product_id")]
        num_items = sum(int(li.get("quantity", 1)) for li in line_items) or len(content_ids) or 1
        order_number = order_data.get("order_number")

        event = {
            "event_name": "Purchase",
            "event_time": int(time.time()),
            "event_id": str(shopify_order_id),  # Dedup with browser pixel
            "action_source": "website",
            "user_data": _build_user_data(
                email=customer_email, phone=customer_phone,
                first_name=customer_first_name, last_name=customer_last_name,
                client_ip=client_ip, user_agent=user_agent, fbp=fbp, fbc=fbc,
            ),
            "custom_data": {
                "currency": currency,
                "value": total,
                "content_ids": content_ids,
                "content_type": "product",
                "num_items": num_items,
                **({"order_id": str(order_number)} if order_number else {}),
            },
        }

        if settings.shopify_shop_domain:
            event["event_source_url"] = f"https://{settings.shopify_shop_domain}/orders/{shopify_order_id}"

        return await self._post_event(event)
