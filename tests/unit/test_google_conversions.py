"""Tests for Google Ads Offline Conversions client."""

from unittest.mock import MagicMock, patch

import pytest

from src.marketing.google_conversions import GoogleOfflineConversions


@pytest.fixture
def conversions_client():
    """Create GoogleOfflineConversions with configured settings."""
    with patch("src.marketing.google_conversions.settings") as mock_settings:
        mock_settings.google_ads_developer_token = "test-token"
        mock_settings.google_ads_client_id = "test-client-id"
        mock_settings.google_ads_client_secret = "test-secret"
        mock_settings.google_ads_refresh_token = "test-refresh"
        mock_settings.google_ads_customer_id = "1234567890"
        mock_settings.google_ads_conversion_action_id = "8129127831"
        yield GoogleOfflineConversions()


@pytest.fixture
def unconfigured_client():
    """GoogleOfflineConversions with missing credentials."""
    with patch("src.marketing.google_conversions.settings") as mock_settings:
        mock_settings.google_ads_developer_token = ""
        mock_settings.google_ads_client_id = ""
        mock_settings.google_ads_client_secret = ""
        mock_settings.google_ads_refresh_token = ""
        mock_settings.google_ads_customer_id = ""
        mock_settings.google_ads_conversion_action_id = ""
        yield GoogleOfflineConversions()


async def test_send_conversion_success(conversions_client):
    """Should upload conversion and return True on success."""
    mock_response = MagicMock()
    mock_response.partial_failure_error = None

    mock_service = MagicMock()
    mock_service.upload_click_conversions.return_value = mock_response

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service
    mock_ga_client.get_type.side_effect = lambda t: MagicMock()

    conversions_client._client = mock_ga_client

    result = await conversions_client.send_purchase_conversion(
        gclid="CjwKCAtest123",
        conversion_date_time="2026-03-29T10:00:00-04:00",
        conversion_value=2850.00,
        order_id="12345",
    )

    assert result is True
    mock_service.upload_click_conversions.assert_called_once()


async def test_send_conversion_partial_failure(conversions_client):
    """Partial failure should return False."""
    mock_error = MagicMock()
    mock_error.message = "CONVERSION_ACTION_NOT_FOUND"

    mock_response = MagicMock()
    mock_response.partial_failure_error = mock_error

    mock_service = MagicMock()
    mock_service.upload_click_conversions.return_value = mock_response

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service
    mock_ga_client.get_type.side_effect = lambda t: MagicMock()

    conversions_client._client = mock_ga_client

    result = await conversions_client.send_purchase_conversion(
        gclid="CjwKCAtest456",
        conversion_date_time="2026-03-29T10:00:00-04:00",
        conversion_value=150.00,
    )

    assert result is False


async def test_send_conversion_no_gclid(conversions_client):
    """Empty gclid should return False without making API call."""
    result = await conversions_client.send_purchase_conversion(
        gclid="",
        conversion_date_time="2026-03-29T10:00:00-04:00",
        conversion_value=100.00,
    )
    assert result is False


async def test_send_conversion_not_configured(unconfigured_client):
    """Unconfigured client should return False without API call."""
    result = await unconfigured_client.send_purchase_conversion(
        gclid="CjwKCAtest789",
        conversion_date_time="2026-03-29T10:00:00-04:00",
        conversion_value=100.00,
    )
    assert result is False


async def test_send_conversion_exception_handled(conversions_client):
    """API exception should be caught, return False (non-blocking)."""
    mock_service = MagicMock()
    mock_service.upload_click_conversions.side_effect = RuntimeError("network error")

    mock_ga_client = MagicMock()
    mock_ga_client.get_service.return_value = mock_service
    mock_ga_client.get_type.side_effect = lambda t: MagicMock()

    conversions_client._client = mock_ga_client

    result = await conversions_client.send_purchase_conversion(
        gclid="CjwKCAtest000",
        conversion_date_time="2026-03-29T10:00:00-04:00",
        conversion_value=500.00,
    )

    assert result is False
