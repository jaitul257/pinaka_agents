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


@dataclass
class MetaCreativeInsight:
    """Per-ad daily metrics from Meta Insights API (level=ad).

    One row per (day, ad). The same creative can back multiple ads under
    different ad sets — group by meta_creative_id in SQL when needed.
    """
    date: date
    meta_ad_id: str
    meta_creative_id: str | None
    meta_adset_id: str | None
    meta_campaign_id: str | None
    ad_name: str
    creative_name: str
    impressions: int
    reach: int
    clicks: int
    spend: float
    ctr: float
    cpm: float
    cpc: float
    frequency: float
    view_content_count: int
    atc_count: int
    ic_count: int
    purchase_count: int
    purchase_value: float
    raw: dict[str, Any]


class MetaAdsClient:
    """Pull ad spend and campaign performance from Meta Marketing API.

    Reuses the existing CAPI access token (same system user token works
    for both Conversions API and Marketing API if ads_read scope is granted).
    """

    def __init__(self):
        self._access_token = settings.meta_ads_access_token or settings.meta_capi_access_token
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


    async def get_creative_insights(self, target_date: date) -> list[MetaCreativeInsight]:
        """Pull per-ad metrics for a day. Level=ad, one row per live ad.

        Returns [] if no ads ran that day. Never raises on empty — only on
        actual API failures.
        """
        if not self.is_configured:
            raise MetaAdsError("Meta Ads not configured")

        if not self._scope_verified:
            await self.verify_token_scope()

        date_str = target_date.isoformat()
        url = (
            f"https://graph.facebook.com/{self._api_version}"
            f"/{self._ad_account_id}/insights"
        )
        params = {
            "level": "ad",
            "fields": (
                "ad_id,ad_name,adset_id,campaign_id,"
                "impressions,reach,clicks,ctr,cpm,cpc,spend,frequency,"
                "actions,action_values"
            ),
            "time_range": f'{{"since":"{date_str}","until":"{date_str}"}}',
            "limit": "500",
            "access_token": self._access_token,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException:
            raise MetaAdsError(f"Meta Insights timeout for {date_str}")
        except httpx.HTTPError as e:
            raise MetaAdsError(f"Meta Insights network error: {e}")

        if response.status_code == 401:
            raise MetaAdsError("Meta Insights 401 — token missing ads_read or expired")
        if response.status_code == 429:
            raise MetaAdsError("Meta Insights rate limited (429)")
        if response.status_code != 200:
            raise MetaAdsError(
                f"Meta Insights error {response.status_code}: {response.text[:300]}"
            )

        rows = response.json().get("data", [])

        # Fetch creative_id per ad with a follow-up call — insights doesn't return it.
        # We batch-request ads once, keyed by ad_id, to enrich each row.
        ad_ids = [r.get("ad_id") for r in rows if r.get("ad_id")]
        creative_map = await self._fetch_creative_refs(ad_ids) if ad_ids else {}

        results: list[MetaCreativeInsight] = []
        for row in rows:
            ad_id = str(row.get("ad_id", ""))
            if not ad_id:
                continue
            cref = creative_map.get(ad_id, {})
            actions = _actions_to_map(row.get("actions") or [])
            action_vals = _actions_to_map(row.get("action_values") or [])

            results.append(MetaCreativeInsight(
                date=target_date,
                meta_ad_id=ad_id,
                meta_creative_id=cref.get("creative_id"),
                meta_adset_id=str(row.get("adset_id")) if row.get("adset_id") else None,
                meta_campaign_id=str(row.get("campaign_id")) if row.get("campaign_id") else None,
                ad_name=str(row.get("ad_name", "") or ""),
                creative_name=cref.get("creative_name", ""),
                impressions=int(row.get("impressions", 0) or 0),
                reach=int(row.get("reach", 0) or 0),
                clicks=int(row.get("clicks", 0) or 0),
                spend=float(row.get("spend", 0) or 0),
                ctr=float(row.get("ctr", 0) or 0),
                cpm=float(row.get("cpm", 0) or 0),
                cpc=float(row.get("cpc", 0) or 0),
                frequency=float(row.get("frequency", 0) or 0),
                view_content_count=int(actions.get("view_content", 0)),
                atc_count=int(actions.get("add_to_cart", 0)),
                ic_count=int(actions.get("initiate_checkout", 0)),
                purchase_count=int(actions.get("purchase", 0)),
                purchase_value=float(action_vals.get("purchase", 0)),
                raw=row,
            ))

        logger.info("Meta Insights: %d ads with data on %s", len(results), date_str)
        return results

    async def _fetch_creative_refs(self, ad_ids: list[str]) -> dict[str, dict]:
        """Batch-fetch {ad_id: {creative_id, creative_name}} via a single request.

        Meta's /insights endpoint doesn't include creative.id/name even when
        requested, so we do a separate /ads read. Small N at our scale.
        """
        if not ad_ids:
            return {}

        # Deduplicate, cap at 50 to stay under URL limits
        unique_ids = list(dict.fromkeys(ad_ids))[:50]
        ids_param = ",".join(unique_ids)

        url = f"https://graph.facebook.com/{self._api_version}"
        params = {
            "ids": ids_param,
            "fields": "creative{id,name}",
            "access_token": self._access_token,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params)
            if response.status_code != 200:
                logger.warning(
                    "Creative ref fetch failed %d; continuing without creative_ids",
                    response.status_code,
                )
                return {}
            body = response.json()
        except Exception:
            logger.exception("Creative ref fetch error; continuing without creative_ids")
            return {}

        result: dict[str, dict] = {}
        for ad_id, payload in body.items():
            if not isinstance(payload, dict):
                continue
            creative = payload.get("creative") or {}
            result[ad_id] = {
                "creative_id": str(creative.get("id")) if creative.get("id") else None,
                "creative_name": str(creative.get("name", "") or ""),
            }
        return result


def _actions_to_map(actions: list[dict]) -> dict[str, float]:
    """Flatten Meta's actions list: [{action_type, value}, ...] → {type: value}."""
    out: dict[str, float] = {}
    for item in actions or []:
        t = item.get("action_type")
        v = item.get("value")
        if t and v is not None:
            try:
                out[t] = float(v)
            except (TypeError, ValueError):
                pass
    return out


class MetaAdsError(Exception):
    """Raised when Meta Marketing API calls fail."""
    pass
