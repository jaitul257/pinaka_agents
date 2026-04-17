"""Integration tests for /cron/weekly-creative-rotation."""

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


def _variant_mock(label: str):
    v = MagicMock()
    v.to_db_row.return_value = {
        "sku": "TEST", "variant_label": label, "headline": "h",
        "primary_text": "p", "description": "d", "cta": "SHOP_NOW",
        "image_url": "https://cdn.shopify.com/x.jpg",
        "generation_batch_id": "batch-123", "brand_dna_hash": "hash",
    }
    return v


@patch.dict("os.environ", {"AUTO_ROTATE_CREATIVES": "false"})
def test_rotation_respects_disabled_flag(client, cron_headers):
    response = client.post("/cron/weekly-creative-rotation", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "disabled"


@patch("src.api.app.AsyncDatabase")
def test_rotation_back_pressure_on_too_many_pending(mock_db_cls, client, cron_headers):
    mock_db = AsyncMock()
    mock_db.count_pending_ad_creatives.return_value = 7  # over the 5-cap
    mock_db_cls.return_value = mock_db

    response = client.post("/cron/weekly-creative-rotation", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"
    assert data["reason"] == "too_many_pending"
    assert data["pending"] == 7


@patch("src.api.app.AsyncDatabase")
def test_rotation_skips_when_no_stale_products(mock_db_cls, client, cron_headers):
    mock_db = AsyncMock()
    mock_db.count_pending_ad_creatives.return_value = 0
    mock_db.get_next_rotation_sku.return_value = None
    mock_db_cls.return_value = mock_db

    response = client.post("/cron/weekly-creative-rotation", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"
    assert data["reason"] == "no_stale_products"


@patch("src.marketing.ad_generator.fetch_top_performers")
@patch("src.marketing.ad_generator.AdCreativeGenerator")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_rotation_generates_and_posts_slack(
    mock_db_cls, mock_slack_cls, mock_gen_cls, mock_fetch_top, client, cron_headers,
):
    mock_db = AsyncMock()
    mock_db.count_pending_ad_creatives.return_value = 0
    mock_db.get_next_rotation_sku.return_value = {
        "sku": "DTB-LBG-7-14YKG",
        "name": "Diamond Tennis — Yellow Gold",
        "status": "active",
        "images": [{"src": "https://cdn.shopify.com/bracelet.jpg"}],
        "_last_creative_at": "2026-04-01T12:00:00+00:00",
    }
    mock_db.create_ad_creative_batch.return_value = []
    mock_db_cls.return_value = mock_db

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    # Claude generator returns 3 variants
    mock_gen = MagicMock()
    mock_gen.generate.return_value = (
        [_variant_mock("A"), _variant_mock("B"), _variant_mock("C")],
        "batch-xyz", "dna-hash",
    )
    mock_gen_cls.return_value = mock_gen

    # 2 historical top performers feed the prompt
    mock_fetch_top.return_value = [
        {"name": "Variant A", "ctr": 1.2, "purchases": 1, "spend": 50.0},
        {"name": "Variant C", "ctr": 0.9, "purchases": 0, "spend": 40.0},
    ]

    with patch("src.agents.observations.observe", new_callable=AsyncMock):
        response = client.post("/cron/weekly-creative-rotation", headers=cron_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["sku"] == "DTB-LBG-7-14YKG"
    assert data["batch_id"] == "batch-xyz"
    assert data["variant_count"] == 3
    assert data["top_performers_used"] == 2

    # Generator got the top performers injected
    call_kwargs = mock_gen.generate.call_args.kwargs
    assert len(call_kwargs["top_performers"]) == 2

    # Rows were written
    mock_db.create_ad_creative_batch.assert_awaited_once()
    rows_arg = mock_db.create_ad_creative_batch.call_args[0][0]
    assert len(rows_arg) == 3

    # Slack got notified with a link to the dashboard
    mock_slack.send_blocks.assert_awaited_once()
    blocks = mock_slack.send_blocks.call_args[0][0]
    joined = str(blocks)
    assert "DTB-LBG-7-14YKG" in joined
    assert "/dashboard/ad-creatives" in joined


@patch("src.marketing.ad_generator.fetch_top_performers")
@patch("src.marketing.ad_generator.AdCreativeGenerator")
@patch("src.api.app.AsyncDatabase")
def test_rotation_handles_generator_error(
    mock_db_cls, mock_gen_cls, mock_fetch_top, client, cron_headers,
):
    from src.marketing.ad_generator import AdGeneratorError

    mock_db = AsyncMock()
    mock_db.count_pending_ad_creatives.return_value = 0
    mock_db.get_next_rotation_sku.return_value = {
        "sku": "TEST", "name": "Test", "status": "active",
        "images": [{"src": "https://cdn.shopify.com/x.jpg"}],
        "_last_creative_at": None,
    }
    mock_db_cls.return_value = mock_db

    mock_gen = MagicMock()
    mock_gen.generate.side_effect = AdGeneratorError("No images")
    mock_gen_cls.return_value = mock_gen
    mock_fetch_top.return_value = []

    response = client.post("/cron/weekly-creative-rotation", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "No images" in data["error"]
    mock_db.create_ad_creative_batch.assert_not_called()
