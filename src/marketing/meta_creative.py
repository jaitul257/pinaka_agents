"""Meta Marketing API — Ad Creative push client (Phase 6.1).

Creates Ad Creative objects in the Pinaka Meta Ad Account via POST /act_{id}/adcreatives.
Every new creative is pushed with `status=PAUSED` first (soft-pause window) so the
founder can flip to ACTIVE with a second click from the dashboard — this is the direct
mitigation for the "2am Friday typo" scenario flagged by the eng subagent.

Reuses the same Meta System User token as Conversions API and Meta Catalog sync.
Requires ads_management scope on the token and a linked Facebook Page ID in settings.

Note: Creatives are NOT attached to any ad set automatically. The founder manually attaches
them in Meta Ads Manager. This keeps the automation safe — nothing goes live without two
human actions (approve in dashboard, attach in Ads Manager).
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.settings import settings
from src.marketing.ad_generator import AdVariant

logger = logging.getLogger(__name__)


class MetaCreativeError(Exception):
    """Raised when Meta Ad Creative API calls fail (4xx, 5xx, embedded error, network)."""


@dataclass
class MetaCreativeResult:
    """Return value from create_creative()."""
    creative_id: str
    status: str  # 'PAUSED' (default) or 'ACTIVE'
    object_story_id: str | None = None


def _slugify(name: str) -> str:
    """URL-friendly product handle (Shopify convention). Mirrors meta_catalog._slugify."""
    slug = (name or "").lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-") or "product"


class MetaCreativeClient:
    """Push approved ad variants to the Meta Ad Account as Ad Creative objects."""

    def __init__(self):
        self._access_token = settings.meta_ads_access_token or settings.meta_capi_access_token
        self._ad_account_id = settings.meta_ad_account_id
        self._page_id = settings.meta_facebook_page_id
        self._api_version = settings.meta_graph_api_version

    @property
    def is_configured(self) -> bool:
        return bool(self._access_token and self._ad_account_id and self._page_id)

    def _require_configured(self) -> None:
        if not self._access_token:
            raise MetaCreativeError(
                "Meta Ad Creative client not configured: META_ADS_ACCESS_TOKEN missing"
            )
        if not self._ad_account_id:
            raise MetaCreativeError(
                "Meta Ad Creative client not configured: META_AD_ACCOUNT_ID missing"
            )
        if not self._page_id:
            raise MetaCreativeError(
                "Meta Ad Creative client not configured: META_FACEBOOK_PAGE_ID missing. "
                "Create a Facebook Page for Pinaka Jewellery and link it to the Business Portfolio."
            )

    def _build_payload(
        self, variant: AdVariant, product_name: str, product_sku: str, batch_id: str
    ) -> dict[str, Any]:
        """Build the POST body for /adcreatives. object_story_spec.link_data is the
        foreground format — standard for single-image link ads."""
        slug = _slugify(product_name)
        storefront = settings.storefront_domain or "pinakajewellery.com"
        # Normalize storefront: ensure no trailing slash, no scheme
        storefront_host = storefront.replace("https://", "").replace("http://", "").rstrip("/")
        product_link = f"https://{storefront_host}/products/{slug}"

        payload = {
            "name": f"Pinaka — {product_name} — Variant {variant.variant_label} — {batch_id[:8]}"[:200],
            "object_story_spec": {
                "page_id": self._page_id,
                "link_data": {
                    "message": variant.primary_text,
                    "link": product_link,
                    "name": variant.headline,
                    "description": variant.description or "",
                    "picture": variant.image_url,
                    "call_to_action": {
                        "type": variant.cta,
                        "value": {"link": product_link},
                    },
                },
            },
            "status": "PAUSED",  # Soft-pause window — founder must flip to ACTIVE manually
        }
        return payload

    async def create_creative(
        self, variant: AdVariant, product_name: str, product_sku: str, batch_id: str
    ) -> MetaCreativeResult:
        """POST to /act_{id}/adcreatives. Returns creative_id on success.

        Always creates in PAUSED state. Use set_creative_active() to flip to live.

        Raises:
            MetaCreativeError: on 4xx, 5xx, embedded error in 200 response, or timeout.
        """
        self._require_configured()

        url = (
            f"https://graph.facebook.com/{self._api_version}"
            f"/{self._ad_account_id}/adcreatives"
        )
        payload = self._build_payload(variant, product_name, product_sku, batch_id)
        payload["access_token"] = self._access_token

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, data=payload)
        except httpx.TimeoutException:
            raise MetaCreativeError(
                f"Meta /adcreatives timeout for variant {variant.variant_label}"
            )
        except httpx.HTTPError as e:
            raise MetaCreativeError(f"Meta /adcreatives network error: {e}")

        return self._handle_create_response(response, variant)

    def _handle_create_response(
        self, response: httpx.Response, variant: AdVariant
    ) -> MetaCreativeResult:
        body = {}
        try:
            body = response.json()
        except Exception:
            pass

        if response.status_code == 401:
            raise MetaCreativeError(
                "Meta /adcreatives 401 auth failed — token expired or missing ads_management scope"
            )
        if response.status_code == 429:
            raise MetaCreativeError("Meta /adcreatives 429 rate limited — retry later")
        if response.status_code >= 500:
            raise MetaCreativeError(
                f"Meta /adcreatives {response.status_code} server error (retry may help): "
                f"{response.text[:300]}"
            )
        if response.status_code >= 400:
            err_msg = body.get("error", {}).get("message", response.text[:300])
            raise MetaCreativeError(
                f"Meta /adcreatives {response.status_code}: {err_msg}"
            )

        # 200 OK — but Meta occasionally returns embedded error envelopes
        if "error" in body:
            err = body["error"]
            raise MetaCreativeError(
                f"Meta /adcreatives 200 with embedded error: "
                f"{err.get('message', 'unknown')} (type={err.get('type')}, code={err.get('code')})"
            )

        creative_id = body.get("id")
        if not creative_id:
            raise MetaCreativeError(
                f"Meta /adcreatives 200 but no 'id' in response body: {body}"
            )

        logger.info(
            "Meta ad creative created (PAUSED): variant=%s id=%s",
            variant.variant_label, creative_id,
        )
        return MetaCreativeResult(
            creative_id=str(creative_id),
            status="PAUSED",
            object_story_id=body.get("object_story_id"),
        )

    async def set_creative_status(
        self, creative_id: str, status: str
    ) -> MetaCreativeResult:
        """Flip a creative to ACTIVE or back to PAUSED via POST /{creative_id}.

        Used by the dashboard "Go Live" and "Pause on Meta" buttons.
        """
        if status not in ("ACTIVE", "PAUSED", "DELETED"):
            raise MetaCreativeError(f"Invalid Meta creative status: {status}")

        self._require_configured()

        url = f"https://graph.facebook.com/{self._api_version}/{creative_id}"
        payload = {
            "status": status,
            "access_token": self._access_token,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, data=payload)
        except httpx.TimeoutException:
            raise MetaCreativeError(f"Meta /{creative_id} timeout")
        except httpx.HTTPError as e:
            raise MetaCreativeError(f"Meta /{creative_id} network error: {e}")

        if response.status_code >= 400:
            body = {}
            try:
                body = response.json()
            except Exception:
                pass
            err_msg = body.get("error", {}).get("message", response.text[:300])
            raise MetaCreativeError(
                f"Meta /{creative_id} status update failed ({response.status_code}): {err_msg}"
            )

        logger.info("Meta ad creative %s → status=%s", creative_id, status)
        return MetaCreativeResult(creative_id=creative_id, status=status)
