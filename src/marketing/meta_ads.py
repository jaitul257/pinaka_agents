"""Meta Marketing API client for pulling ad spend and campaign metrics.

Uses the same system user token as CAPI (meta_capi_access_token).
Requires ads_read scope on the token.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from src.core.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class MetaAdSpendResult:
    """Daily ad spend metrics from Meta Marketing API."""
    date: date
    spend: float
    impressions: int
    clicks: int
    purchase_roas: float  # Meta-reported ROAS (may differ from blended)
    source: str = "api"


class MetaAdsClient:
    """Pull ad spend and campaign performance from Meta Marketing API.

    Reuses the existing CAPI access token (same system user token works
    for both Conversions API and Marketing API if ads_read scope is granted).
    """

    def __init__(self):
        self._access_token = settings.meta_capi_access_token
        self._ad_account_id = settings.meta_ad_account_id
        self._api_version = settings.meta_graph_api_version
        self._scope_verified = False

    @property
    def is_configured(self) -> bool:
        return bool(self._access_token and self._ad_account_id)

    async def verify_token_scope(self) -> bool:
        """Check that the access token has ads_read scope for Marketing API.

        Calls Meta's /debug_token endpoint. Logs warning if ads_read is missing.
        Returns True if scope is present, False otherwise.
        """
        if not self._access_token:
            return False

        url = f"https://graph.facebook.com/{self._api_version}/debug_token"
        params = {
            "input_token": self._access_token,
            "access_token": self._access_token,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.warning(
                    "Meta token debug failed: %d %s",
                    response.status_code, response.text[:200],
                )
                return False

            data = response.json().get("data", {})
            scopes = data.get("scopes", [])

            if "ads_read" not in scopes:
                logger.warning(
                    "Meta token missing ads_read scope. Current scopes: %s. "
                    "Marketing API calls will fail. Re-generate token with ads_read.",
                    scopes,
                )
                return False

            self._scope_verified = True
            return True

        except Exception:
            logger.exception("Failed to verify Meta token scopes")
            return False

    async def get_daily_spend(self, target_date: date) -> MetaAdSpendResult:
        """Pull ad spend metrics for a single day from Meta Marketing API.

        Calls GET /{ad_account_id}/insights with date range = target_date.
        Returns MetaAdSpendResult with spend, impressions, clicks, purchase_roas.

        Raises:
            MetaAdsError: On API failure (auth, rate limit, network).
        """
        if not self.is_configured:
            raise MetaAdsError("Meta Ads not configured (missing token or ad_account_id)")

        # Verify scope on first call
        if not self._scope_verified:
            await self.verify_token_scope()

        date_str = target_date.isoformat()
        url = (
            f"https://graph.facebook.com/{self._api_version}"
            f"/{self._ad_account_id}/insights"
        )
        params = {
            "fields": "spend,impressions,clicks,purchase_roas",
            "time_range": f'{{"since":"{date_str}","until":"{date_str}"}}',
            "access_token": self._access_token,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException:
            raise MetaAdsError(f"Meta Ads API timeout for {date_str}")
        except httpx.HTTPError as e:
            raise MetaAdsError(f"Meta Ads API network error: {e}")

        if response.status_code == 401:
            raise MetaAdsError(
                f"Meta Ads API auth failed (401). Token may be expired or missing ads_read scope."
            )
        if response.status_code == 429:
            raise MetaAdsError("Meta Ads API rate limited (429). Retry later.")
        if response.status_code != 200:
            raise MetaAdsError(
                f"Meta Ads API error {response.status_code}: {response.text[:300]}"
            )

        body = response.json()
        data_list = body.get("data", [])

        if not data_list:
            # No spend on this day (campaign paused, no impressions, etc.)
            logger.info("No Meta ad data for %s (campaign may be paused)", date_str)
            return MetaAdSpendResult(
                date=target_date,
                spend=0.0,
                impressions=0,
                clicks=0,
                purchase_roas=0.0,
            )

        row = data_list[0]
        # purchase_roas comes as [{"action_type": "...", "value": "..."}] list
        roas_list = row.get("purchase_roas", [])
        roas_value = 0.0
        if roas_list and isinstance(roas_list, list):
            roas_value = float(roas_list[0].get("value", 0))

        result = MetaAdSpendResult(
            date=target_date,
            spend=float(row.get("spend", 0)),
            impressions=int(row.get("impressions", 0)),
            clicks=int(row.get("clicks", 0)),
            purchase_roas=roas_value,
        )

        logger.info(
            "Meta ad spend for %s: $%.2f (%d impressions, %d clicks, ROAS %.2f)",
            date_str, result.spend, result.impressions, result.clicks, result.purchase_roas,
        )
        return result


class MetaAdsError(Exception):
    """Raised when Meta Marketing API calls fail."""
    pass
