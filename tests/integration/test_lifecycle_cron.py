"""Integration tests for /cron/lifecycle-daily + /cron/welcome-daily +
/api/attribution/submit anniversary extension + Slack lifecycle actions."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.core.settings import settings


@pytest.fixture
def cron_headers():
    return {"X-Cron-Secret": settings.cron_secret or "test-secret"}


@pytest.fixture
def client():
    with patch("src.core.database.get_supabase", return_value=MagicMock()), \
         patch("src.core.slack.AsyncWebClient"), \
         patch.object(settings, "cron_secret", "test-secret"), \
         patch.object(settings, "slack_signing_secret", ""):
        from src.api.app import app
        with TestClient(app) as c:
            yield c


# ── /cron/lifecycle-daily ──

@patch("src.customer.lifecycle.LifecycleOrchestrator")
def test_lifecycle_cron_no_candidates(mock_orch_cls, client, cron_headers):
    mock_orch = AsyncMock()
    mock_orch.find_all_candidates.return_value = []
    mock_orch_cls.return_value = mock_orch

    response = client.post("/cron/lifecycle-daily", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["candidates_found"] == 0
    assert data["slack_posts"] == 0


@patch("src.api.app.SlackNotifier")
@patch("src.customer.lifecycle.LifecycleOrchestrator")
def test_lifecycle_cron_posts_to_slack_per_candidate(
    mock_orch_cls, mock_slack_cls, client, cron_headers,
):
    from src.customer.lifecycle import (
        DraftedEmail, LifecycleCandidate, TRIGGER_CARE, TRIGGER_REFERRAL,
    )

    candidates = [
        LifecycleCandidate(
            customer_id=1, customer_email="a@x.com", customer_name="A",
            trigger=TRIGGER_CARE, days_since_purchase=10,
            last_order_items="Bracelet", last_order_number="5001",
        ),
        LifecycleCandidate(
            customer_id=2, customer_email="b@x.com", customer_name="B",
            trigger=TRIGGER_REFERRAL, days_since_purchase=60,
            last_order_items="Bracelet", last_order_number="5002",
        ),
    ]
    draft_map = {
        TRIGGER_CARE: DraftedEmail(candidate=candidates[0], subject="Care", body="body A"),
        TRIGGER_REFERRAL: DraftedEmail(candidate=candidates[1], subject="Refer", body="body B"),
    }

    mock_orch = AsyncMock()
    mock_orch.find_all_candidates.return_value = candidates
    mock_orch.draft.side_effect = lambda c: draft_map[c.trigger]
    mock_orch_cls.return_value = mock_orch

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/lifecycle-daily", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["candidates_found"] == 2
    assert data["slack_posts"] == 2
    assert mock_slack.send_lifecycle_email_review.await_count == 2


# ── /cron/welcome-daily ──

@patch("src.customer.welcome.WelcomeSeriesEngine")
def test_welcome_cron_reports_results(mock_engine_cls, client, cron_headers):
    mock_engine = AsyncMock()
    mock_engine.send_due.return_value = {
        "candidates": 3, "sent": 2, "failed": 0, "skipped_missing_template": 1,
    }
    mock_engine_cls.return_value = mock_engine

    response = client.post("/cron/welcome-daily", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["sent"] == 2


# ── /api/attribution/submit with anniversary ──

@patch("src.api.app.AsyncDatabase")
def test_submit_captures_anniversary(mock_db_cls, client):
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {
        "id": 99, "shopify_order_id": 5001, "buyer_email": "bride@example.com",
        "customer_id": 42,
    }
    mock_db.insert_attribution.return_value = {"id": 11}
    mock_db.upsert_customer_anniversary.return_value = {"id": 33}

    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "channel_primary": "instagram",
                "purchase_reason": "anniversary",
                "anniversary_date": "2027-06-15",
                "relationship": "wedding_anniversary",
            },
        )

    assert response.status_code == 200
    # Attribution row got the anniversary fields
    attr_row = mock_db.insert_attribution.call_args[0][0]
    assert attr_row["anniversary_date"] == "2027-06-15"
    assert attr_row["relationship"] == "wedding_anniversary"
    # And customer_anniversaries got upserted
    mock_db.upsert_customer_anniversary.assert_awaited_once()
    anniv_row = mock_db.upsert_customer_anniversary.call_args[0][0]
    assert anniv_row["customer_id"] == 42
    assert anniv_row["anniversary_date"] == "2027-06-15"


@patch("src.api.app.AsyncDatabase")
def test_submit_ignores_anniversary_when_reason_mismatches(mock_db_cls, client):
    """Date only accepted when purchase_reason is anniversary/engagement/milestone."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {
        "id": 99, "shopify_order_id": 5001, "buyer_email": "x@y.com", "customer_id": 42,
    }
    mock_db.insert_attribution.return_value = {"id": 11}

    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "channel_primary": "instagram",
                "purchase_reason": "self_purchase",  # not anniversary
                "anniversary_date": "2027-06-15",
            },
        )

    assert response.status_code == 200
    attr_row = mock_db.insert_attribution.call_args[0][0]
    assert attr_row["anniversary_date"] is None
    mock_db.upsert_customer_anniversary.assert_not_called()


@patch("src.api.app.AsyncDatabase")
def test_submit_rejects_malformed_anniversary(mock_db_cls, client):
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {
        "id": 99, "shopify_order_id": 5001, "customer_id": 42,
    }
    mock_db.insert_attribution.return_value = {"id": 11}

    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "channel_primary": "instagram",
                "purchase_reason": "anniversary",
                "anniversary_date": "not-a-date",
            },
        )

    assert response.status_code == 200
    attr_row = mock_db.insert_attribution.call_args[0][0]
    assert attr_row["anniversary_date"] is None


@patch("src.api.app.AsyncDatabase")
def test_submit_rejects_far_future_anniversary(mock_db_cls, client):
    """Dates >5y out are silently ignored (likely typos)."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {
        "id": 99, "shopify_order_id": 5001, "customer_id": 42,
    }
    mock_db.insert_attribution.return_value = {"id": 11}

    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post(
            "/api/attribution/submit",
            json={
                "shopify_order_id": "5001",
                "channel_primary": "instagram",
                "purchase_reason": "anniversary",
                "anniversary_date": "2099-12-31",
            },
        )

    assert response.status_code == 200
    attr_row = mock_db.insert_attribution.call_args[0][0]
    assert attr_row["anniversary_date"] is None
