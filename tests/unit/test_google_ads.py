"""Tests for Google Ads API ad spend client."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.marketing.google_ads import GoogleAdsClient, GoogleAdsError, GoogleAdSpendResult


@pytest.fixture
def google_client():
    """Create GoogleAdsClient with configured settings."""
    with patch("src.marketing.google_ads.settings") as mock_settings:
        mock_settings.google_ads_developer_token = "test-token"
        mock_settings.google_ads_client_id = "test-client-id"
        mock_settings.google_ads_client_secret = "test-secret"
        mock_settings.google_ads_refresh_token = "test-refresh"
        mock_settings.google_ads_customer_id = "1234567890"
        yield GoogleAdsClient()


@pytest.fixture
def unconfigured_client():
    """GoogleAdsClient with missing credentials."""
    with patch("src.marketing.google_ads.settings") as mock_settings:
        mock_settings.google_ads_developer_token = ""
        mock_settings.google_ads_client_id = ""
        mock_settings.google_ads_client_secret = ""
        mock_settings.google_ads_refresh_token = ""
        mock_settings.google_ads_customer_id = ""
        yield GoogleAdsClient()


def _make_mock_row(cost_micros=0, impressions=0, clicks=0, conversions=0.0):
    """Build a mock Google Ads API result row."""
    row = MagicMock()
    row.metrics.cost_micros = cost_micros
    row.metrics.impressions = impressions
    row.metrics.clicks = clicks
    row.metrics.conversions = conversions
    return row


async def test_get_daily_spend_success(google_client):
    """Should parse spend from cost_micros and return correct metrics."""
    mock_batch = MagicMock()
    mock_batch.results = [
        _make_mock_row(cost_micros=42_500_000, impressions=3200, clicks=85, conversions=5.0),
    ]

    mock_service = MagicMock()
    mock_service.search_stream.return_value = [mock_batch]

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service

    google_client._client = mock_ga_client

    result = await google_client.get_daily_spend(date(2026, 3, 29))

    assert isinstance(result, GoogleAdSpendResult)
    assert result.spend == 42.50
    assert result.impressions == 3200
    assert result.clicks == 85
    assert result.conversions == 5.0
    assert result.date == date(2026, 3, 29)


async def test_get_daily_spend_empty(google_client):
    """No data for the day should return zeros."""
    mock_batch = MagicMock()
    mock_batch.results = []

    mock_service = MagicMock()
    mock_service.search_stream.return_value = [mock_batch]

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service

    google_client._client = mock_ga_client

    result = await google_client.get_daily_spend(date(2026, 3, 29))

    assert result.spend == 0.0
    assert result.impressions == 0
    assert result.clicks == 0


async def test_get_daily_spend_multiple_rows(google_client):
    """Multiple campaign rows should be summed."""
    mock_batch = MagicMock()
    mock_batch.results = [
        _make_mock_row(cost_micros=10_000_000, impressions=1000, clicks=30, conversions=2.0),
        _make_mock_row(cost_micros=15_000_000, impressions=2000, clicks=50, conversions=3.0),
    ]

    mock_service = MagicMock()
    mock_service.search_stream.return_value = [mock_batch]

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service

    google_client._client = mock_ga_client

    result = await google_client.get_daily_spend(date(2026, 3, 29))

    assert result.spend == 25.00
    assert result.impressions == 3000
    assert result.clicks == 80
    assert result.conversions == 5.0


async def test_get_daily_spend_auth_error(google_client):
    """Auth error should raise GoogleAdsError."""
    mock_service = MagicMock()
    mock_service.search_stream.side_effect = Exception("AUTHENTICATION_ERROR: invalid token")

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service

    google_client._client = mock_ga_client

    with pytest.raises(GoogleAdsError, match="auth failed"):
        await google_client.get_daily_spend(date(2026, 3, 29))


async def test_get_daily_spend_not_configured(unconfigured_client):
    """Should raise GoogleAdsError when credentials are missing."""
    with pytest.raises(GoogleAdsError, match="not configured"):
        await unconfigured_client.get_daily_spend(date(2026, 3, 29))
