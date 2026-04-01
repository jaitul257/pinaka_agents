"""Google Ads API client for pulling ad spend and campaign metrics.

Uses the google-ads library with OAuth2 credentials.
Requires Basic access developer token for production (test token for dev).
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from src.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class GoogleAdSpendResult:
    """Daily ad spend metrics from Google Ads API."""
    date: date
    spend: float  # In account currency (USD)
    impressions: int
    clicks: int
    conversions: float
    source: str = "api"


class GoogleAdsClient:
    """Pull ad spend and campaign performance from Google Ads Reporting API."""

    def __init__(self):
        self._developer_token = settings.google_ads_developer_token
        self._client_id = settings.google_ads_client_id
        self._client_secret = settings.google_ads_client_secret
        self._refresh_token = settings.google_ads_refresh_token
        self._customer_id = settings.google_ads_customer_id
        self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self._developer_token
            and self._client_id
            and self._client_secret
            and self._refresh_token
            and self._customer_id
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

    async def get_daily_spend(self, target_date: date) -> GoogleAdSpendResult:
        """Pull ad spend metrics for a single day from Google Ads Reporting API.

        Uses GAQL (Google Ads Query Language) to fetch aggregate metrics.

        Raises:
            GoogleAdsError: On API failure.
        """
        if not self.is_configured:
            raise GoogleAdsError("Google Ads not configured (missing credentials)")

        import asyncio

        try:
            result = await asyncio.to_thread(self._fetch_spend_sync, target_date)
            return result
        except GoogleAdsError:
            raise
        except Exception as e:
            raise GoogleAdsError(f"Google Ads API error: {e}")

    def _fetch_spend_sync(self, target_date: date) -> GoogleAdSpendResult:
        """Synchronous spend fetch (runs in thread via asyncio.to_thread)."""
        client = self._get_client()
        ga_service = client.get_service("GoogleAdsService")

        date_str = target_date.strftime("%Y-%m-%d")
        query = f"""
            SELECT
                metrics.cost_micros,
                metrics.impressions,
                metrics.clicks,
                metrics.conversions
            FROM customer
            WHERE segments.date = '{date_str}'
        """

        try:
            stream = ga_service.search_stream(
                customer_id=self._customer_id,
                query=query,
            )
        except Exception as e:
            error_msg = str(e)
            if "AUTHENTICATION_ERROR" in error_msg or "AuthenticationError" in error_msg:
                raise GoogleAdsError(f"Google Ads auth failed: {error_msg[:300]}")
            raise GoogleAdsError(f"Google Ads query failed: {error_msg[:300]}")

        total_cost_micros = 0
        total_impressions = 0
        total_clicks = 0
        total_conversions = 0.0

        for batch in stream:
            for row in batch.results:
                total_cost_micros += row.metrics.cost_micros
                total_impressions += row.metrics.impressions
                total_clicks += row.metrics.clicks
                total_conversions += row.metrics.conversions

        spend = total_cost_micros / 1_000_000  # Micros to dollars

        result = GoogleAdSpendResult(
            date=target_date,
            spend=spend,
            impressions=total_impressions,
            clicks=total_clicks,
            conversions=total_conversions,
        )

        logger.info(
            "Google ad spend for %s: $%.2f (%d impressions, %d clicks, %.1f conversions)",
            date_str, result.spend, result.impressions, result.clicks, result.conversions,
        )
        return result


class GoogleAdsError(Exception):
    """Raised when Google Ads API calls fail."""
    pass
