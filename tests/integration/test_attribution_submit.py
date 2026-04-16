"""Integration tests for POST /api/attribution/submit — post-purchase survey capture."""

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


@patch("src.api.app.AsyncDatabase")
def test_submit_valid_response(mock_db_cls, client):
    """Happy path: valid order_id + channel → 200 + observation written."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {
        "id": 1,
        "shopify_order_id": 5001,
        "buyer_email": "customer@example.com",
    }
    mock_db.insert_attribution.return_value = {"id": 10}

    with patch("src.agents.observations.observe", new_callable=AsyncMock) as mock_observe:
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "customer_email": "customer@example.com",
                "channel_primary": "instagram",
                "channel_detail": "@somecreator",
                "purchase_reason": "self_purchase",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    mock_db.insert_attribution.assert_called_once()
    call_args = mock_db.insert_attribution.call_args[0][0]
    assert call_args["shopify_order_id"] == "5001"
    assert call_args["channel_primary"] == "instagram"
    assert call_args["channel_detail"] == "@somecreator"
    assert call_args["submitted_via"] == "thankyou_page"
    mock_observe.assert_awaited_once()


@patch("src.api.app.AsyncDatabase")
def test_submit_missing_order_id(mock_db_cls, client):
    """Missing shopify_order_id → 400."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    response = client.post(
        "/api/attribution/submit",
        json={"channel_primary": "instagram"},
    )
    assert response.status_code == 400
    assert "shopify_order_id" in response.json()["detail"]


@patch("src.api.app.AsyncDatabase")
def test_submit_invalid_channel(mock_db_cls, client):
    """Channel not in allowed set → 400."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    response = client.post(
        "/api/attribution/submit",
        json={"shopify_order_id": "5001", "channel_primary": "carrier_pigeon"},
    )
    assert response.status_code == 400
    assert "channel_primary" in response.json()["detail"]


@patch("src.api.app.AsyncDatabase")
def test_submit_order_not_found(mock_db_cls, client):
    """Order doesn't exist → 404 (spam filter)."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = None

    response = client.post(
        "/api/attribution/submit",
        json={"shopify_order_id": "99999", "channel_primary": "instagram"},
    )
    assert response.status_code == 404
    mock_db.insert_attribution.assert_not_called()


@patch("src.api.app.AsyncDatabase")
def test_submit_non_numeric_order_id(mock_db_cls, client):
    """Non-numeric order id → 404 (can't match Shopify numeric IDs)."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    response = client.post(
        "/api/attribution/submit",
        json={"shopify_order_id": "abc", "channel_primary": "instagram"},
    )
    assert response.status_code == 404


@patch("src.api.app.AsyncDatabase")
def test_submit_duplicate_is_idempotent(mock_db_cls, client):
    """Submitting twice (refresh) → 200 already_recorded, not 500."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {"id": 1, "shopify_order_id": 5001}
    mock_db.insert_attribution.side_effect = Exception("duplicate key value violates unique constraint")

    response = client.post(
        "/api/attribution/submit",
        json={"shopify_order_id": "5001", "channel_primary": "instagram"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "already_recorded"}


@patch("src.api.app.AsyncDatabase")
def test_submit_invalid_reason_falls_back(mock_db_cls, client):
    """Unknown purchase_reason → normalized to 'other'."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {"id": 1, "shopify_order_id": 5001}
    mock_db.insert_attribution.return_value = {"id": 10}

    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "channel_primary": "instagram",
                "purchase_reason": "made_up_reason",
            },
        )

    assert response.status_code == 200
    call_args = mock_db.insert_attribution.call_args[0][0]
    assert call_args["purchase_reason"] == "other"


def test_submit_malformed_json(client):
    """Non-JSON body → 400."""
    response = client.post(
        "/api/attribution/submit",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


@patch("src.api.app.AsyncDatabase")
def test_submit_truncates_long_free_text(mock_db_cls, client):
    """channel_detail > 500 chars is truncated, not rejected."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {"id": 1, "shopify_order_id": 5001}
    mock_db.insert_attribution.return_value = {"id": 10}

    long_detail = "x" * 1000
    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "channel_primary": "other",
                "channel_detail": long_detail,
            },
        )

    assert response.status_code == 200
    call_args = mock_db.insert_attribution.call_args[0][0]
    assert len(call_args["channel_detail"]) == 500
