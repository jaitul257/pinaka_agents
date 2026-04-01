"""Tests for Meta Marketing API ad spend client."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.marketing.meta_ads import MetaAdsClient, MetaAdsError, MetaAdSpendResult


@pytest.fixture
def meta_client():
    """Create MetaAdsClient with configured settings."""
    with patch("src.marketing.meta_ads.settings") as mock_settings:
        mock_settings.meta_capi_access_token = "test-token-123"
        mock_settings.meta_ad_account_id = "act_987654321"
        mock_settings.meta_graph_api_version = "v21.0"
        client = MetaAdsClient()
        client._scope_verified = True  # Skip scope check in spend tests
        yield client


@pytest.fixture
def unconfigured_client():
    """MetaAdsClient with missing credentials."""
    with patch("src.marketing.meta_ads.settings") as mock_settings:
        mock_settings.meta_capi_access_token = ""
        mock_settings.meta_ad_account_id = ""
        mock_settings.meta_graph_api_version = "v21.0"
        yield MetaAdsClient()


def _mock_response(status_code: int = 200, json_data: dict = None, text: str = ""):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or str(json_data)
    return resp


async def test_get_daily_spend_success(meta_client):
    """Should parse spend, impressions, clicks, and purchase_roas from API response."""
    api_response = {
        "data": [{
            "spend": "42.50",
            "impressions": "3200",
            "clicks": "85",
            "purchase_roas": [{"action_type": "omni_purchase", "value": "3.75"}],
            "date_start": "2026-03-29",
            "date_stop": "2026-03-29",
        }]
    }

    mock_resp = _mock_response(200, api_response)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_client):
        result = await meta_client.get_daily_spend(date(2026, 3, 29))

    assert isinstance(result, MetaAdSpendResult)
    assert result.spend == 42.50
    assert result.impressions == 3200
    assert result.clicks == 85
    assert result.purchase_roas == 3.75
    assert result.date == date(2026, 3, 29)
    assert result.source == "api"


async def test_get_daily_spend_empty_data(meta_client):
    """No spend on this day (campaign paused) should return zeros."""
    api_response = {"data": []}

    mock_resp = _mock_response(200, api_response)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_client):
        result = await meta_client.get_daily_spend(date(2026, 3, 29))

    assert result.spend == 0.0
    assert result.impressions == 0
    assert result.clicks == 0
    assert result.purchase_roas == 0.0


async def test_get_daily_spend_auth_failure(meta_client):
    """401 response should raise MetaAdsError with auth message."""
    mock_resp = _mock_response(401, text="Invalid token")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(MetaAdsError, match="auth failed"):
            await meta_client.get_daily_spend(date(2026, 3, 29))


async def test_get_daily_spend_rate_limit(meta_client):
    """429 response should raise MetaAdsError with rate limit message."""
    mock_resp = _mock_response(429, text="Rate limited")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(MetaAdsError, match="rate limited"):
            await meta_client.get_daily_spend(date(2026, 3, 29))


async def test_get_daily_spend_timeout(meta_client):
    """httpx timeout should raise MetaAdsError."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(MetaAdsError, match="timeout"):
            await meta_client.get_daily_spend(date(2026, 3, 29))


async def test_get_daily_spend_not_configured(unconfigured_client):
    """Should raise MetaAdsError when credentials are missing."""
    with pytest.raises(MetaAdsError, match="not configured"):
        await unconfigured_client.get_daily_spend(date(2026, 3, 29))


async def test_verify_token_scope_success():
    """Should return True when ads_read scope is present."""
    with patch("src.marketing.meta_ads.settings") as mock_settings:
        mock_settings.meta_capi_access_token = "test-token"
        mock_settings.meta_ad_account_id = "act_123"
        mock_settings.meta_graph_api_version = "v21.0"
        client = MetaAdsClient()

    debug_response = {
        "data": {
            "scopes": ["ads_read", "ads_management", "pages_read_engagement"],
            "type": "USER",
            "is_valid": True,
        }
    }

    mock_resp = _mock_response(200, debug_response)
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_http):
        result = await client.verify_token_scope()

    assert result is True
    assert client._scope_verified is True


async def test_verify_token_scope_missing_ads_read():
    """Should return False and log warning when ads_read scope is missing."""
    with patch("src.marketing.meta_ads.settings") as mock_settings:
        mock_settings.meta_capi_access_token = "test-token"
        mock_settings.meta_ad_account_id = "act_123"
        mock_settings.meta_graph_api_version = "v21.0"
        client = MetaAdsClient()

    debug_response = {
        "data": {
            "scopes": ["pages_read_engagement"],
            "type": "USER",
            "is_valid": True,
        }
    }

    mock_resp = _mock_response(200, debug_response)
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_http):
        result = await client.verify_token_scope()

    assert result is False
    assert client._scope_verified is False


async def test_get_daily_spend_no_roas_field(meta_client):
    """Should handle missing purchase_roas field gracefully."""
    api_response = {
        "data": [{
            "spend": "25.00",
            "impressions": "1500",
            "clicks": "40",
            # No purchase_roas field
        }]
    }

    mock_resp = _mock_response(200, api_response)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_ads.httpx.AsyncClient", return_value=mock_client):
        result = await meta_client.get_daily_spend(date(2026, 3, 29))

    assert result.spend == 25.00
    assert result.purchase_roas == 0.0
