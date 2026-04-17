"""Integration tests for Phase 10 endpoints: RFM cron, VOC cron, customer profile API."""

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
         patch.object(settings, "slack_signing_secret", ""), \
         patch.object(settings, "dashboard_password", "test-pw"):
        from src.api.app import app
        with TestClient(app) as c:
            yield c


# ── /cron/rfm-compute ──

@patch("src.customer.rfm.RFMScorer")
def test_rfm_cron_success(mock_scorer_cls, client, cron_headers):
    mock_scorer = AsyncMock()
    mock_scorer.run_daily.return_value = {
        "scored": 12, "segment_counts": {"champion": 2, "new": 10},
        "computed_date": "2026-04-16",
    }
    mock_scorer_cls.return_value = mock_scorer

    response = client.post("/cron/rfm-compute", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["scored"] == 12
    assert data["segment_counts"]["champion"] == 2


@patch("src.customer.rfm.RFMScorer")
def test_rfm_cron_handles_exception(mock_scorer_cls, client, cron_headers):
    mock_scorer = AsyncMock()
    mock_scorer.run_daily.side_effect = Exception("DB hiccup")
    mock_scorer_cls.return_value = mock_scorer

    response = client.post("/cron/rfm-compute", headers=cron_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "error"


# ── /cron/voc-mine ──

@patch("src.customer.voc.VoiceOfCustomer")
def test_voc_cron_reports_counts(mock_voc_cls, client, cron_headers):
    from datetime import date
    mock_voc = AsyncMock()
    result = MagicMock()
    result.week_ending = date(2026, 4, 15)
    result.themes = [{"theme": "X"}, {"theme": "Y"}]
    result.messages_analyzed = 8
    result.chats_analyzed = 3
    result.survey_responses = 4
    mock_voc.run_weekly.return_value = result
    mock_voc_cls.return_value = mock_voc

    response = client.post("/cron/voc-mine", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["themes"] == 2
    assert data["week_ending"] == "2026-04-15"


# ── /api/customer/{id}/profile ──

def test_profile_endpoint_requires_auth(client):
    response = client.get("/api/customer/42/profile")
    assert response.status_code == 401


@patch("src.customer.profile.CustomerProfileBuilder")
def test_profile_endpoint_returns_json(mock_builder_cls, client):
    from src.customer.profile import CustomerProfile
    mock_builder = MagicMock()
    mock_profile = CustomerProfile(
        customer_id=42, shopify_customer_id=7001, email="a@b.com", name="A",
        phone="", lifecycle_stage="first_purchase", accepts_marketing=True,
        created_at="2026-01-01", generated_at="2026-04-16T00:00:00",
    )
    mock_builder.for_customer = AsyncMock(return_value=mock_profile)
    mock_builder.to_json = MagicMock(return_value={"customer_id": 42, "email": "a@b.com"})
    mock_builder_cls.return_value = mock_builder

    client.cookies.set("dash_token", "test-pw")
    response = client.get("/api/customer/42/profile")
    assert response.status_code == 200
    assert response.json()["customer_id"] == 42


@patch("src.customer.profile.CustomerProfileBuilder")
def test_profile_endpoint_404_for_unknown(mock_builder_cls, client):
    mock_builder = MagicMock()
    mock_builder.for_customer = AsyncMock(return_value=None)
    mock_builder_cls.return_value = mock_builder

    client.cookies.set("dash_token", "test-pw")
    response = client.get("/api/customer/99999/profile")
    assert response.status_code == 404
