"""Google Ads Offline Conversions client for server-side purchase tracking.

Uploads purchase conversion events to Google Ads when a gclid is present
in the order's landing page URL. This is the Google equivalent of Meta CAPI.

Only fires for paid orders with a gclid. Non-blocking: failures are logged
but never block order processing.
"""

import logging
from typing import Any

from src.core.settings import settings

logger = logging.getLogger(__name__)


class GoogleOfflineConversions:
    """Upload offline purchase conversions to Google Ads."""

    def __init__(self):
        self._developer_token = settings.google_ads_developer_token
        self._client_id = settings.google_ads_client_id
        self._client_secret = settings.google_ads_client_secret
        self._refresh_token = settings.google_ads_refresh_token
        self._customer_id = settings.google_ads_customer_id
        self._conversion_action_id = settings.google_ads_conversion_action_id
        self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self._developer_token
            and self._client_id
            and self._client_secret
            and self._refresh_token
            and self._customer_id
            and self._conversion_action_id
        )

    def _get_client(self):
        """Lazy-init the google-ads client."""
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient as GAdsClient

            self._client = GAdsClient.load_from_dict({
                "developer_token": self._developer_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "use_proto_plus": True,
            })
        return self._client

    async def send_purchase_conversion(
        self,
        gclid: str,
        conversion_date_time: str,
        conversion_value: float,
        order_id: str = "",
    ) -> bool:
        """Upload a single purchase conversion to Google Ads.

        Args:
            gclid: Google Click ID from the order's landing page URL.
            conversion_date_time: ISO 8601 timestamp of the purchase.
            conversion_value: Order total in USD.
            order_id: Shopify order ID for deduplication.

        Returns True on success, False on failure. Never raises.
        """
        if not self.is_configured:
            logger.info("Google Offline Conversions not configured, skipping")
            return False

        if not gclid:
            return False

        import asyncio

        try:
            success = await asyncio.to_thread(
                self._upload_sync, gclid, conversion_date_time, conversion_value, order_id
            )
            return success
        except Exception:
            logger.exception("Google offline conversion upload failed for gclid=%s", gclid[:20])
            return False

    def _upload_sync(
        self,
        gclid: str,
        conversion_date_time: str,
        conversion_value: float,
        order_id: str,
    ) -> bool:
        """Synchronous upload (runs in thread)."""
        client = self._get_client()
        conversion_upload_service = client.get_service("ConversionUploadService")

        # Build the click conversion
        click_conversion = client.get_type("ClickConversion")
        click_conversion.gclid = gclid
        click_conversion.conversion_action = (
            f"customers/{self._customer_id}/conversionActions/{self._conversion_action_id}"
        )
        click_conversion.conversion_date_time = conversion_date_time
        click_conversion.conversion_value = conversion_value
        click_conversion.currency_code = "USD"

        if order_id:
            click_conversion.order_id = str(order_id)

        # Upload
        request = client.get_type("UploadClickConversionsRequest")
        request.customer_id = self._customer_id
        request.conversions = [click_conversion]
        request.partial_failure = True

        response = conversion_upload_service.upload_click_conversions(request=request)

        # Check for partial failure errors
        if response.partial_failure_error:
            logger.error(
                "Google conversion partial failure for gclid=%s: %s",
                gclid[:20], response.partial_failure_error.message,
            )
            return False

        logger.info(
            "Google offline conversion uploaded: gclid=%s, value=$%.2f, order=%s",
            gclid[:20], conversion_value, order_id,
        )
        return True
