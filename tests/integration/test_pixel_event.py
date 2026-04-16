"""Integration tests for POST /api/pixel/event — CAPI relay from the storefront."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.core.settings import settings


@pytest.fixture
def client():
    with patch("src.core.database.get_supabase", return_value=MagicMock()), \
         patch("src.core.slack.AsyncWebClient"), \
         patch.object(settings, "slack_signing_secret", ""):
        from src.api.app import app
        with TestClient(app) as c:
            yield c


def test_pixel_event_invalid_event_name(client):
    response = client.post(
        "/api/pixel/event",
        json={"event_name": "SomethingFake", "event_id": "x"},
    )
    assert response.status_code == 400
    assert "event_name" in response.json()["detail"]


def test_pixel_event_missing_event_id(client):
    response = client.post(
        "/api/pixel/event",
        json={"event_name": "ViewContent"},
    )
    assert response.status_code == 400
    assert "event_id" in response.json()["detail"]


def test_pixel_event_unconfigured_returns_skipped(client):
    """CAPI not configured → returns 200 skipped (no crash)."""
    with patch("src.marketing.meta_capi.settings") as mock_settings:
        mock_settings.meta_pixel_id = ""
        mock_settings.meta_capi_access_token = ""
        mock_settings.meta_graph_api_version = "v25.0"
        mock_settings.shopify_shop_domain = ""
        response = client.post(
            "/api/pixel/event",
            json={
                "event_name": "ViewContent", "event_id": "vc_1",
                "product_id": "p1", "value": 4900,
            },
        )
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


def test_pixel_event_view_content_success(client):
    """Happy path: ViewContent relays to CAPI."""
    with patch("src.marketing.meta_capi.MetaConversionsAPI") as mock_cls:
        mock_api = MagicMock()
        mock_api.is_configured = True
        mock_api.send_view_content = AsyncMock(return_value=True)
        mock_cls.return_value = mock_api

        response = client.post(
            "/api/pixel/event",
            json={
                "event_name": "ViewContent",
                "event_id": "vc_1",
                "product_id": "prod_123",
                "value": 4900,
                "customer_email": "test@example.com",
                "fbp": "fb.1.x",
                "source_url": "https://pinakajewellery.com/products/x",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "sent", "event_name": "ViewContent", "event_id": "vc_1"}
    mock_api.send_view_content.assert_awaited_once()
    kwargs = mock_api.send_view_content.call_args.kwargs
    assert kwargs["product_id"] == "prod_123"
    assert kwargs["value"] == 4900.0
    assert kwargs["event_id"] == "vc_1"
    assert kwargs["fbp"] == "fb.1.x"


def test_pixel_event_initiate_checkout_content_ids(client):
    """IC accepts an array of content_ids."""
    with patch("src.marketing.meta_capi.MetaConversionsAPI") as mock_cls:
        mock_api = MagicMock()
        mock_api.is_configured = True
        mock_api.send_initiate_checkout = AsyncMock(return_value=True)
        mock_cls.return_value = mock_api

        response = client.post(
            "/api/pixel/event",
            json={
                "event_name": "InitiateCheckout",
                "event_id": "ic_5",
                "content_ids": ["a", "b"],
                "value": 10000,
            },
        )
    assert response.status_code == 200
    kwargs = mock_api.send_initiate_checkout.call_args.kwargs
    assert kwargs["content_ids"] == ["a", "b"]


def test_pixel_event_capi_failure_returns_failed(client):
    """CAPI returns False → response status=failed (not 500)."""
    with patch("src.marketing.meta_capi.MetaConversionsAPI") as mock_cls:
        mock_api = MagicMock()
        mock_api.is_configured = True
        mock_api.send_add_to_cart = AsyncMock(return_value=False)
        mock_cls.return_value = mock_api

        response = client.post(
            "/api/pixel/event",
            json={"event_name": "AddToCart", "event_id": "atc_1", "product_id": "p", "value": 100},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_pixel_event_malformed_value_defaults_to_zero(client):
    """Non-numeric value → coerced to 0, doesn't crash."""
    with patch("src.marketing.meta_capi.MetaConversionsAPI") as mock_cls:
        mock_api = MagicMock()
        mock_api.is_configured = True
        mock_api.send_view_content = AsyncMock(return_value=True)
        mock_cls.return_value = mock_api

        response = client.post(
            "/api/pixel/event",
            json={"event_name": "ViewContent", "event_id": "x", "product_id": "p", "value": "not_a_number"},
        )
    assert response.status_code == 200
    kwargs = mock_api.send_view_content.call_args.kwargs
    assert kwargs["value"] == 0.0
