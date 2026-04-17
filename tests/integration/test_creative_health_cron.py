"""Integration tests for /cron/creative-health + /cron/sync-creative-metrics."""

from datetime import date, timedelta
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


def _metrics_row(ad_id: str, d: date, **kw) -> dict:
    return {
        "date": d.isoformat(),
        "meta_ad_id": ad_id,
        "meta_creative_id": kw.get("creative_id", "cr_" + ad_id),
        "ad_name": kw.get("ad_name", ad_id),
        "creative_name": "",
        "impressions": kw.get("impressions", 0),
        "reach": kw.get("reach", 0),
        "clicks": kw.get("clicks", 0),
        "spend": kw.get("spend", 0.0),
        "ctr": kw.get("ctr", 0.0),
        "cpm": 0.0, "cpc": 0.0, "frequency": kw.get("frequency", 0.0),
        "view_content_count": 0, "atc_count": kw.get("atc", 0),
        "ic_count": 0, "purchase_count": kw.get("purchase_count", 0),
        "purchase_value": 0.0,
    }


@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_creative_health_no_fatigue(mock_slack_cls, mock_db_cls, client, cron_headers):
    """Healthy metrics → no Slack post, status=ok."""
    today = date.today()
    healthy_rows = [
        _metrics_row("ad_1", today - timedelta(days=d),
                     impressions=1000, reach=800, clicks=15,
                     spend=3.0, purchase_count=1)
        for d in range(1, 8)
    ]
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_creative_metrics_range.return_value = healthy_rows

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/creative-health", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["flags"] == 0
    mock_slack.send_blocks.assert_not_called()


@patch("src.agents.observations.observe", new_callable=AsyncMock)
@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_creative_health_fatigue_posts_slack(
    mock_slack_cls, mock_db_cls, mock_observe, client, cron_headers
):
    """Fatigued ad → Slack alert + observation."""
    today = date.today()
    # Dead-spend ad: $15/day * 7 days = $105 spend, no purchases, high impressions
    dead_rows = [
        _metrics_row("ad_dead", today - timedelta(days=d),
                     impressions=1500, reach=500, clicks=20,
                     spend=15.0, purchase_count=0)
        for d in range(1, 8)
    ]
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_creative_metrics_range.return_value = dead_rows

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/creative-health", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["flags"] == 1
    assert data["by_reason"]["dead_spend"] == 1
    mock_slack.send_blocks.assert_awaited_once()
    # Inspect Slack text — dead spend detail should mention the ad
    blocks_call = mock_slack.send_blocks.call_args[0][0]
    joined_text = str(blocks_call)
    assert "Dead spend" in joined_text or "dead_spend" in joined_text
    mock_observe.assert_awaited()


@patch("src.marketing.meta_ads.MetaAdsClient")
@patch("src.api.app.AsyncDatabase")
def test_sync_creative_metrics_writes_rows(
    mock_db_cls, mock_meta_cls, client, cron_headers
):
    """Happy path: Meta returns 2 ads → 2 rows written."""
    from src.marketing.meta_ads import MetaCreativeInsight

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    target_date = date.today() - timedelta(days=1)
    mock_meta = MagicMock()
    mock_meta.is_configured = True
    mock_meta.get_creative_insights = AsyncMock(return_value=[
        MetaCreativeInsight(
            date=target_date, meta_ad_id="ad_1", meta_creative_id="cr_1",
            meta_adset_id="aset_1", meta_campaign_id="c_1",
            ad_name="Variant A", creative_name="Creative A",
            impressions=500, reach=400, clicks=5, spend=10.0,
            ctr=1.0, cpm=20.0, cpc=2.0, frequency=1.25,
            view_content_count=2, atc_count=1, ic_count=1,
            purchase_count=0, purchase_value=0, raw={},
        ),
        MetaCreativeInsight(
            date=target_date, meta_ad_id="ad_2", meta_creative_id="cr_2",
            meta_adset_id="aset_1", meta_campaign_id="c_1",
            ad_name="Variant B", creative_name="Creative B",
            impressions=300, reach=280, clicks=3, spend=7.0,
            ctr=1.0, cpm=23.3, cpc=2.33, frequency=1.07,
            view_content_count=1, atc_count=0, ic_count=0,
            purchase_count=0, purchase_value=0, raw={},
        ),
    ])
    mock_meta_cls.return_value = mock_meta

    response = client.post("/cron/sync-creative-metrics", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["ads_with_data"] == 2
    assert data["rows_written"] == 2
    assert mock_db.upsert_creative_metrics.await_count == 2


@patch("src.marketing.meta_ads.MetaAdsClient")
def test_sync_creative_metrics_unconfigured(mock_meta_cls, client, cron_headers):
    """Meta not configured → skip, not error."""
    mock_meta = MagicMock()
    mock_meta.is_configured = False
    mock_meta_cls.return_value = mock_meta

    response = client.post("/cron/sync-creative-metrics", headers=cron_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"


@patch("src.marketing.meta_ads.MetaAdsClient")
def test_sync_creative_metrics_meta_error(mock_meta_cls, client, cron_headers):
    """Meta API error → status=error, no crash."""
    from src.marketing.meta_ads import MetaAdsError

    mock_meta = MagicMock()
    mock_meta.is_configured = True
    mock_meta.get_creative_insights = AsyncMock(side_effect=MetaAdsError("rate limited"))
    mock_meta_cls.return_value = mock_meta

    response = client.post("/cron/sync-creative-metrics", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "rate limited" in data["error"]
