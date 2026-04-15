"""Integration tests for FastAPI endpoints.

Tests the full request/response cycle through the API with mocked
external dependencies (Database, ShopifyClient, Slack, SendGrid, etc.).
"""

import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.core.settings import settings


# ── Fixtures ──

@pytest.fixture
def cron_headers():
    """Headers with valid cron secret."""
    return {"X-Cron-Secret": settings.cron_secret or "test-secret"}


@pytest.fixture
def bad_cron_headers():
    """Headers with wrong cron secret."""
    return {"X-Cron-Secret": "wrong-secret"}


@pytest.fixture
def client():
    """TestClient with cron secret set and Slack verification disabled."""
    with patch("src.core.database.get_supabase", return_value=MagicMock()), \
         patch("src.core.slack.AsyncWebClient"), \
         patch.object(settings, "cron_secret", "test-secret"), \
         patch.object(settings, "slack_signing_secret", ""):
        from src.api.app import app
        with TestClient(app) as c:
            yield c


# ── Health ──

def test_health_endpoint(client):
    """Health endpoint should return module statuses."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "modules" in data
    assert "shopify" in data["modules"]
    for module in data["modules"].values():
        assert module["status"] == "ok"


# ── Cron Auth ──

def test_cron_requires_secret(client, bad_cron_headers):
    """Cron endpoints should reject requests without valid secret."""
    response = client.post("/cron/daily-stats", headers=bad_cron_headers)
    assert response.status_code == 403


def test_cron_missing_header(client):
    """Cron endpoints should reject requests with no header."""
    response = client.post("/cron/daily-stats")
    assert response.status_code == 422  # Missing required header


# ── Cron: Daily Stats ──

@patch("src.api.app.AsyncDatabase")
def test_cron_daily_stats(mock_db_cls, client, cron_headers):
    """Daily stats cron should aggregate yesterday's orders."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_total_revenue.return_value = 4050.0
    mock_db.get_orders_by_status.return_value = []
    mock_db.get_customers_by_lifecycle.return_value = []
    mock_db.get_repeat_customer_count.return_value = 3

    response = client.post("/cron/daily-stats", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["module"] == "stats"
    mock_db.upsert_daily_stats.assert_called_once()


# ── Cron: Reconcile Orders ──

@patch("src.api.app.SlackNotifier")
@patch("src.api.app.ShopifyClient")
@patch("src.api.app.AsyncDatabase")
def test_cron_reconcile_orders(mock_db_cls, mock_shopify_cls, mock_slack_cls, client, cron_headers):
    """Reconcile cron should process orders missed by webhooks."""
    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_orders.return_value = [
        {"id": 5001, "customer": {}, "email": "buyer@example.com",
         "total_price": "2850.00", "line_items": []},
    ]
    mock_shopify.close = AsyncMock()

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = None  # Not yet processed

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    with patch("src.api.shopify_webhooks._process_order", new_callable=AsyncMock) as mock_process:
        response = client.post("/cron/reconcile-orders", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["reconciled"] == 1
        mock_process.assert_called_once()


@patch("src.api.app.SlackNotifier")
@patch("src.api.app.ShopifyClient")
@patch("src.api.app.AsyncDatabase")
def test_cron_reconcile_skips_existing(mock_db_cls, mock_shopify_cls, mock_slack_cls, client, cron_headers):
    """Reconcile should skip orders already in the database."""
    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_orders.return_value = [
        {"id": 5002, "customer": {}, "email": "buyer@example.com"},
    ]
    mock_shopify.close = AsyncMock()

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {"shopify_order_id": 5002}

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    with patch("src.api.shopify_webhooks._process_order", new_callable=AsyncMock) as mock_process:
        response = client.post("/cron/reconcile-orders", headers=cron_headers)
        assert response.status_code == 200
        assert response.json()["reconciled"] == 0
        mock_process.assert_not_called()


# ── Cron: Crafting Updates ──

@patch("src.api.app.MessageClassifier")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_crafting_updates(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Crafting update cron should send Slack reviews for eligible orders."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_orders_needing_crafting_update.return_value = [
        {
            "shopify_order_id": 5001,
            "total": 2850.0,
            "created_at": "2026-03-26T10:00:00",
            "customers": {"name": "Jane Doe", "email": "jane@example.com", "lifecycle_stage": "first_purchase"},
        }
    ]

    mock_classifier = MagicMock()
    mock_class_cls.return_value = mock_classifier
    mock_classifier.draft_response = AsyncMock(return_value="Your bracelet is being handcrafted...")

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/crafting-updates", headers=cron_headers)
    assert response.status_code == 200
    assert response.json()["sent"] == 1
    mock_slack.send_crafting_update_review.assert_called_once()


# ── Cron: Abandoned Carts ──

@patch("src.api.app.MessageClassifier")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_abandoned_carts(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Abandoned cart cron should flag carts for Slack review."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.mark_abandoned_carts.return_value = 0
    mock_db.get_abandoned_carts_pending_recovery.return_value = [
        {
            "id": 1,
            "shopify_checkout_token": "tok_abc123",
            "customer_email": "buyer@example.com",
            "cart_value": 2850.0,
            "items_json": '[{"title": "Diamond Bracelet", "quantity": 1}]',
            "created_at": "2026-03-29T08:00:00",
        }
    ]
    mock_db.get_customer_by_email.return_value = {
        "name": "Jane Doe", "order_count": 2, "lifetime_value": 5700.0,
    }

    mock_classifier = MagicMock()
    mock_class_cls.return_value = mock_classifier
    mock_classifier.draft_response = AsyncMock(
        return_value="Subject: Your beautiful bracelet is waiting\nDear Jane..."
    )

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/abandoned-carts", headers=cron_headers)
    assert response.status_code == 200
    assert response.json()["flagged"] == 1
    mock_slack.send_abandoned_cart_review.assert_called_once()
    mock_db.upsert_cart_event.assert_called()


@patch("src.api.app.MessageClassifier")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_abandoned_carts_skip_low_value(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Abandoned carts under $1 should be skipped."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.mark_abandoned_carts.return_value = 0
    mock_db.get_abandoned_carts_pending_recovery.return_value = [
        {
            "id": 2,
            "shopify_checkout_token": "tok_empty",
            "customer_email": "",
            "cart_value": 0.50,
            "items_json": "[]",
            "created_at": "2026-03-29T08:00:00",
        }
    ]

    response = client.post("/cron/abandoned-carts", headers=cron_headers)
    assert response.status_code == 200
    assert response.json()["flagged"] == 0


@patch("src.api.app.MessageClassifier")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_abandoned_carts_marks_created_as_abandoned(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Cron should transition 'created' carts to 'abandoned' before processing."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.mark_abandoned_carts.return_value = 2  # 2 carts transitioned
    mock_db.get_abandoned_carts_pending_recovery.return_value = []

    response = client.post("/cron/abandoned-carts", headers=cron_headers)
    assert response.status_code == 200
    mock_db.mark_abandoned_carts.assert_called_once_with(60)


# ── Cron: Morning Digest ──

@patch("src.api.app.ShopifyClient")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_morning_digest(mock_db_cls, mock_slack_cls, mock_shopify_cls, client, cron_headers):
    """Morning digest should send revenue, orders, and customer counts."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_stats_range.return_value = [{"revenue": 2850.0, "order_count": 1, "new_customers": 1}]
    mock_db.get_pending_messages.return_value = [
        {"urgency": "urgent", "body": "Damaged item"},
        {"urgency": "normal", "body": "Size question"},
    ]
    mock_db.get_abandoned_carts_pending_recovery.return_value = []

    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_webhooks.return_value = [
        {"topic": "orders/create"},
        {"topic": "customers/create"},
        {"topic": "checkouts/create"},
        {"topic": "refunds/create"},
    ]

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    with patch.object(settings, "webhook_base_url", "https://test.example.com"):
        response = client.post("/cron/morning-digest", headers=cron_headers)
    assert response.status_code == 200
    mock_slack.send_blocks.assert_called_once()
    mock_shopify.get_webhooks.assert_called_once()


@patch("src.api.app.ShopifyClient")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_morning_digest_missing_webhooks(mock_db_cls, mock_slack_cls, mock_shopify_cls, client, cron_headers):
    """Morning digest should alert when webhook subscriptions are missing."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_stats_range.return_value = [{"revenue": 0, "order_count": 0, "new_customers": 0}]
    mock_db.get_pending_messages.return_value = []
    mock_db.get_abandoned_carts_pending_recovery.return_value = []

    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_webhooks.return_value = [
        {"topic": "orders/create"},
    ]  # Missing customers/create, checkouts/create, refunds/create

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    with patch.object(settings, "webhook_base_url", "https://test.example.com"):
        response = client.post("/cron/morning-digest", headers=cron_headers)
    assert response.status_code == 200

    # Webhook health should have attempted re-registration for missing topics
    assert mock_shopify.create_webhook.call_count >= 1


# ── Cron: Weekly Rollup ──

@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_cron_weekly_rollup(mock_db_cls, mock_slack_cls, client, cron_headers):
    """Weekly rollup should aggregate 7 days of stats."""
    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_stats_range.return_value = [
        {"revenue": 2850.0, "order_count": 1, "new_customers": 1, "repeat_customers": 0},
        {"revenue": 1200.0, "order_count": 1, "new_customers": 0, "repeat_customers": 1},
    ]
    mock_db.get_customer_count.return_value = 10
    mock_db.get_repeat_customer_count.return_value = 3

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/weekly-rollup", headers=cron_headers)
    assert response.status_code == 200
    mock_slack.send_blocks.assert_called_once()


# ── Cron: Weekly Finance ──

@patch("src.api.app.FinanceCalculator")
def test_cron_weekly_finance(mock_fin_cls, client, cron_headers):
    """Weekly finance cron should return revenue and profit."""
    mock_calc = MagicMock()
    mock_fin_cls.return_value = mock_calc

    from src.finance.calculator import WeeklyFinanceReport
    mock_calc.run_weekly_finance_report = AsyncMock(return_value=WeeklyFinanceReport(
        start_date=date(2026, 3, 21), end_date=date(2026, 3, 28),
        total_revenue=5700.0, total_net_profit=3800.0, total_orders=2,
    ))

    response = client.post("/cron/weekly-finance", headers=cron_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["revenue"] == 5700.0
    assert data["profit"] == 3800.0
    assert data["orders"] == 2


# ── Webhook: Shopify Orders ──

def test_shopify_order_webhook_missing_hmac(client):
    """Shopify webhook without HMAC header should return 401."""
    response = client.post("/webhook/shopify/orders", json={"id": 1})
    assert response.status_code == 401


# ── Webhook: Slack Approve Response ──

@patch("src.api.app.EmailSender")
@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_approve_response(mock_slack_cls, mock_db_cls, mock_email_cls, client):
    """Approve action should send via SendGrid and tombstone the message."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_pending_messages.return_value = [
        {"id": 1, "customer_email": "buyer@example.com", "buyer_name": "Jane",
         "subject": "Re: Sizing", "ai_draft": "Your order shipped!", "status": "pending_review"}
    ]

    mock_email = MagicMock()
    mock_email_cls.return_value = mock_email
    mock_email.send_service_reply.return_value = True

    payload = json.dumps({
        "actions": [{"action_id": "approve_response", "value": "1"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_email.send_service_reply.assert_called_once()
    mock_db.update_message_status.assert_called_once_with(1, "sent", human_approved=True)
    mock_slack.update_message.assert_called_once()


# ── Webhook: Slack Approve Cart Recovery ──

@patch("src.api.app.EmailSender")
@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_approve_cart_recovery(mock_slack_cls, mock_db_cls, mock_email_cls, client):
    """Approve cart recovery should send email and update cart status."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_cart_by_id.return_value = {
        "id": 42,
        "shopify_checkout_token": "tok_abc",
        "customer_email": "buyer@example.com",
        "cart_value": 2850.0,
        "items_json": '[{"title": "Diamond Bracelet"}]',
    }

    mock_email = MagicMock()
    mock_email_cls.return_value = mock_email
    mock_email.send_cart_recovery.return_value = True

    payload = json.dumps({
        "actions": [{"action_id": "approve_cart_recovery", "value": "42"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_email.send_cart_recovery.assert_called_once()
    mock_db.upsert_cart_event.assert_called()
    mock_slack.update_message.assert_called_once()


# ── Webhook: Slack Skip Cart Recovery ──

@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_skip_cart_recovery(mock_slack_cls, mock_db_cls, client):
    """Skip cart recovery should cancel the recovery status."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_cart_by_id.return_value = {
        "id": 42, "shopify_checkout_token": "tok_abc",
    }

    payload = json.dumps({
        "actions": [{"action_id": "skip_cart_recovery", "value": "42"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_db.upsert_cart_event.assert_called_once()
    call_args = mock_db.upsert_cart_event.call_args[0][0]
    assert call_args["recovery_email_status"] == "cancelled"


# ── Webhook: Slack Approve Crafting Update ──

@patch("src.api.app.EmailSender")
@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_approve_crafting_update(mock_slack_cls, mock_db_cls, mock_email_cls, client):
    """Approve crafting update should send email and update order status."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {
        "shopify_order_id": 5001, "buyer_email": "jane@example.com",
        "buyer_name": "Jane Doe", "customer_id": None,
    }

    mock_email = MagicMock()
    mock_email_cls.return_value = mock_email
    mock_email.send_crafting_update.return_value = True

    payload = json.dumps({
        "actions": [{"action_id": "approve_crafting_update", "value": "5001"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_email.send_crafting_update.assert_called_once()
    mock_db.update_order_status.assert_called_once_with(5001, "crafting_update_sent")


# ── Webhook: Slack Approve Shipment ──

@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_slack_webhook_approve_shipment(mock_db_cls, mock_slack_cls, client):
    """Approve shipment should update order status and tombstone."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    payload = json.dumps({
        "actions": [{"action_id": "approve_shipment", "value": "5001"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_db.update_order_status.assert_called_once_with(5001, "approved_for_shipping")
    mock_slack.update_message.assert_called_once()


# ── Webhook: Slack Hold Order ──

@patch("src.api.app.SlackNotifier")
@patch("src.api.app.AsyncDatabase")
def test_slack_webhook_hold_order(mock_db_cls, mock_slack_cls, client):
    """Hold order should update status to held_for_review."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    payload = json.dumps({
        "actions": [{"action_id": "hold_order", "value": "5001"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_db.update_order_status.assert_called_once_with(5001, "held_for_review")


# ── Webhook: Slack No Actions ──

def test_slack_webhook_no_actions(client):
    """Slack webhook with empty actions should return no_action."""
    payload = json.dumps({"actions": []})
    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    assert response.json()["status"] == "no_action"


# ── Webhook: Slack Reject Response ──

@patch("src.api.app.AsyncDatabase")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_reject_response(mock_slack_cls, mock_db_cls, client):
    """Reject should update message status to rejected."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db

    payload = json.dumps({
        "actions": [{"action_id": "reject_response", "value": "1"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_db.update_message_status.assert_called_once_with(1, "rejected")


# ── Cron: Weekly ROAS ──

def test_cron_weekly_roas(client, cron_headers):
    """ROAS cron should return roas and recommendation fields."""
    with patch("src.marketing.ads.AsyncDatabase") as mock_db_cls, \
         patch("src.marketing.ads.SlackNotifier") as mock_slack_cls:
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_stats_range.return_value = [
            {"ad_spend_google": 10.0, "ad_spend_meta": 15.0, "revenue": 100.0},
        ]
        mock_slack_cls.return_value = AsyncMock()

        response = client.post("/cron/weekly-roas", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["module"] == "ads"
        assert "roas" in data
        assert "recommendation" in data
        assert data["roas"] == 4.0  # 100/(10+15)


def test_cron_weekly_roas_empty_stats(client, cron_headers):
    """Empty stats should return roas=0 and pause recommendation."""
    with patch("src.marketing.ads.AsyncDatabase") as mock_db_cls, \
         patch("src.marketing.ads.SlackNotifier") as mock_slack_cls:
        mock_db_cls.return_value = AsyncMock(get_stats_range=AsyncMock(return_value=[]))
        mock_slack_cls.return_value = AsyncMock()

        response = client.post("/cron/weekly-roas", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["roas"] == 0.0
        assert data["recommendation"] == "pause"


# ── Cron: Sync Ad Spend ──

def test_cron_sync_ad_spend_meta_only(client, cron_headers):
    """When only Meta is configured, should sync Meta spend and skip Google."""
    from src.marketing.meta_ads import MetaAdSpendResult

    mock_meta = MagicMock()
    mock_meta.is_configured = True
    mock_meta.get_daily_spend = AsyncMock(return_value=MetaAdSpendResult(
        date=date(2026, 3, 31), spend=42.50, impressions=1000, clicks=50, purchase_roas=3.5,
    ))

    mock_google = MagicMock()
    mock_google.is_configured = False

    with patch("src.api.app.AsyncDatabase") as mock_db_cls, \
         patch("src.marketing.meta_ads.MetaAdsClient", return_value=mock_meta), \
         patch("src.marketing.google_ads.GoogleAdsClient", return_value=mock_google):

        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_stats_range.return_value = []

        response = client.post("/cron/sync-ad-spend", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["meta_spend"] == 42.50
        assert data["google_spend"] is None


# ── Cron: Sync Meta Catalog ──

def test_cron_sync_meta_catalog_not_configured(client, cron_headers):
    """Unconfigured Meta catalog should return skipped status."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.meta_capi_access_token = ""
        mock_settings.meta_catalog_id = ""
        mock_settings.meta_graph_api_version = "v21.0"
        mock_settings.shopify_shop_domain = "test.myshopify.com"

        response = client.post("/cron/sync-meta-catalog", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert data["module"] == "meta_catalog"


@patch("src.core.database.AsyncDatabase")
def test_cron_sync_meta_catalog_success(mock_db_cls, client, cron_headers):
    """Configured catalog sync should report items synced."""
    from src.marketing.meta_catalog import CatalogSyncResult

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_all_active_products.return_value = [
        {"sku": "SKU-1", "name": "Ring", "category": "Rings", "pricing": {"default": {"retail": 250}},
         "images": ["https://cdn.example.com/ring.jpg"], "story": "A ring.", "tags": [], "shopify_product_id": 1},
    ]

    mock_result = CatalogSyncResult(items_synced=1, items_failed=0, errors=[])

    with patch("src.marketing.meta_catalog.settings") as mock_settings, \
         patch("src.marketing.meta_catalog.MetaCatalogSync.sync_products",
               new_callable=AsyncMock, return_value=mock_result):
        mock_settings.meta_capi_access_token = "test-token"
        mock_settings.meta_catalog_id = "cat_123"
        mock_settings.meta_graph_api_version = "v21.0"
        mock_settings.shopify_shop_domain = "test.myshopify.com"

        response = client.post("/cron/sync-meta-catalog", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["module"] == "meta_catalog"
        assert data["total_products"] == 1
        assert data["items_synced"] == 1


# ── Cron: Sync Google Merchant ──

def test_cron_sync_google_merchant_not_configured(client, cron_headers):
    """Unconfigured Google Merchant should return skipped status."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.google_merchant_id = ""
        mock_settings.google_service_account_path = ""
        mock_settings.google_service_account_json = ""
        mock_settings.shopify_shop_domain = "test.myshopify.com"

        response = client.post("/cron/sync-google-merchant", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert data["module"] == "google_merchant"


@patch("src.api.app.AsyncDatabase")
def test_cron_sync_google_merchant_success(mock_db_cls, client, cron_headers):
    """Configured Merchant sync should report items synced."""
    from src.marketing.google_merchant import MerchantSyncResult

    mock_db = AsyncMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_all_active_products.return_value = [
        {"sku": "SKU-1", "name": "Ring", "category": "Rings",
         "pricing": {"default": {"retail": 250}},
         "images": ["https://cdn.example.com/ring.jpg"],
         "story": "A ring.", "tags": [], "shopify_product_id": 1},
    ]

    mock_result = MerchantSyncResult(items_synced=1, items_failed=0, errors=[])

    with patch("src.marketing.google_merchant.settings") as mock_settings, \
         patch("src.marketing.google_merchant.GoogleMerchantSync.sync_products",
               new_callable=AsyncMock, return_value=mock_result):
        mock_settings.google_merchant_id = "12345"
        mock_settings.google_service_account_path = ""
        mock_settings.google_service_account_json = '{"type": "service_account"}'
        mock_settings.shopify_shop_domain = "test.myshopify.com"

        response = client.post("/cron/sync-google-merchant", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["module"] == "google_merchant"
        assert data["items_synced"] == 1


# ── Virtual Try-On ──

def _make_test_image_b64():
    """Create a tiny valid JPEG as base64 for testing."""
    from PIL import Image
    import io, base64
    img = Image.new("RGB", (100, 100), (200, 180, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_try_on_missing_wrist_image(client):
    """Should return 400 when wrist_image is missing."""
    response = client.post("/api/try-on", json={"product_handle": "test-bracelet"})
    assert response.status_code == 400
    assert "wrist_image" in response.json()["detail"]


def test_try_on_missing_product_handle(client):
    """Should return 400 when product_handle is missing."""
    response = client.post("/api/try-on", json={"wrist_image": "abc123"})
    assert response.status_code == 400
    assert "product_handle" in response.json()["detail"]


def test_try_on_empty_body(client):
    """Should return 400 when body is empty."""
    response = client.post("/api/try-on", json={})
    assert response.status_code == 400


def test_try_on_image_too_large(client):
    """Should return 400 when image exceeds 10MB."""
    huge_b64 = "A" * 14_000_000  # ~10.5MB decoded
    response = client.post("/api/try-on", json={
        "wrist_image": huge_b64,
        "product_handle": "test-bracelet",
    })
    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


@patch("src.api.app.httpx.AsyncClient")
def test_try_on_product_not_found(mock_httpx_cls, client):
    """Should return error when product handle doesn't match any Shopify product."""
    mock_client = AsyncMock()
    mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_response = MagicMock()
    mock_response.json.return_value = {"products": []}
    mock_client.get.return_value = mock_response

    b64 = _make_test_image_b64()
    response = client.post("/api/try-on", json={
        "wrist_image": b64,
        "product_handle": "nonexistent-product",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "not found" in data["message"].lower()


@patch("src.api.app.settings")
@patch("src.api.app.httpx.AsyncClient")
def test_try_on_freepik_not_configured(mock_httpx_cls, mock_settings, client):
    """Should return error when FREEPIK_API_KEY is not set."""
    mock_client = AsyncMock()
    mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    # Shopify returns a product
    mock_shopify_resp = MagicMock()
    mock_shopify_resp.json.return_value = {"products": [{
        "title": "Diamond Tennis Bracelet",
        "images": [{"src": "https://cdn.shopify.com/test.jpg"}],
    }]}
    mock_client.get.return_value = mock_shopify_resp

    mock_settings.shopify_shop_domain = "test.myshopify.com"
    mock_settings.shopify_access_token = "test-token"
    mock_settings.freepik_api_key = ""  # Not configured

    b64 = _make_test_image_b64()
    response = client.post("/api/try-on", json={
        "wrist_image": b64,
        "product_handle": "diamond-tennis-bracelet",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "not configured" in data["message"].lower()


@patch("src.api.app.settings")
@patch("src.api.app.httpx.AsyncClient")
def test_try_on_freepik_submit_fails(mock_httpx_cls, mock_settings, client):
    """Should return error when Freepik API rejects the request."""
    mock_client = AsyncMock()
    mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    # Shopify returns a product
    mock_shopify_resp = MagicMock()
    mock_shopify_resp.json.return_value = {"products": [{
        "title": "Diamond Tennis Bracelet",
        "images": [{"src": "https://cdn.shopify.com/test.jpg"}],
    }]}

    # Freepik returns 400
    mock_freepik_resp = MagicMock()
    mock_freepik_resp.status_code = 400
    mock_freepik_resp.text = "Bad request"

    mock_client.get.return_value = mock_shopify_resp
    mock_client.post.return_value = mock_freepik_resp

    mock_settings.shopify_shop_domain = "test.myshopify.com"
    mock_settings.shopify_access_token = "test-token"
    mock_settings.freepik_api_key = "test-freepik-key"

    b64 = _make_test_image_b64()
    response = client.post("/api/try-on", json={
        "wrist_image": b64,
        "product_handle": "diamond-tennis-bracelet",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "failed" in data["message"].lower()


@patch("src.api.app.asyncio.sleep", new_callable=AsyncMock)
@patch("src.api.app.settings")
@patch("src.api.app.httpx.AsyncClient")
def test_try_on_success(mock_httpx_cls, mock_settings, mock_sleep, client):
    """Full success path: Shopify lookup → mask → Freepik submit → poll → result."""
    mock_client = AsyncMock()
    mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    # Shopify returns a product
    mock_shopify_resp = MagicMock()
    mock_shopify_resp.json.return_value = {"products": [{
        "title": "Diamond Tennis Bracelet - Lab Grown",
        "images": [{"src": "https://cdn.shopify.com/bracelet.jpg"}],
    }]}

    # Freepik submit returns task_id
    mock_freepik_submit = MagicMock()
    mock_freepik_submit.status_code = 200
    mock_freepik_submit.json.return_value = {
        "data": {"task_id": "test-task-123", "status": "CREATED", "generated": []}
    }

    # Freepik poll returns COMPLETED
    mock_freepik_poll = MagicMock()
    mock_freepik_poll.json.return_value = {
        "data": {"task_id": "test-task-123", "status": "COMPLETED",
                 "generated": ["https://cdn-freepik.com/result.jpg"]}
    }

    mock_client.get.side_effect = [mock_shopify_resp, mock_freepik_poll]
    mock_client.post.return_value = mock_freepik_submit

    mock_settings.shopify_shop_domain = "test.myshopify.com"
    mock_settings.shopify_access_token = "test-token"
    mock_settings.freepik_api_key = "test-freepik-key"

    b64 = _make_test_image_b64()
    response = client.post("/api/try-on", json={
        "wrist_image": b64,
        "product_handle": "diamond-tennis-bracelet-lab-grown",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["result_url"] == "https://cdn-freepik.com/result.jpg"
    assert "Diamond Tennis Bracelet" in data["product_title"]


@patch("src.api.app.asyncio.sleep", new_callable=AsyncMock)
@patch("src.api.app.settings")
@patch("src.api.app.httpx.AsyncClient")
def test_try_on_timeout(mock_httpx_cls, mock_settings, mock_sleep, client):
    """Should return timeout error when Freepik never completes within 60s."""
    mock_client = AsyncMock()
    mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_shopify_resp = MagicMock()
    mock_shopify_resp.json.return_value = {"products": [{
        "title": "Diamond Tennis Bracelet",
        "images": [],
    }]}

    mock_freepik_submit = MagicMock()
    mock_freepik_submit.status_code = 200
    mock_freepik_submit.json.return_value = {
        "data": {"task_id": "stuck-task", "status": "CREATED", "generated": []}
    }

    # Poll always returns IN_PROGRESS
    mock_freepik_poll = MagicMock()
    mock_freepik_poll.json.return_value = {
        "data": {"task_id": "stuck-task", "status": "IN_PROGRESS", "generated": []}
    }

    mock_client.get.side_effect = [mock_shopify_resp] + [mock_freepik_poll] * 12
    mock_client.post.return_value = mock_freepik_submit

    mock_settings.shopify_shop_domain = "test.myshopify.com"
    mock_settings.shopify_access_token = "test-token"
    mock_settings.freepik_api_key = "test-freepik-key"

    b64 = _make_test_image_b64()
    response = client.post("/api/try-on", json={
        "wrist_image": b64,
        "product_handle": "test-bracelet",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "timed out" in data["message"].lower()


@patch("src.api.app.asyncio.sleep", new_callable=AsyncMock)
@patch("src.api.app.settings")
@patch("src.api.app.httpx.AsyncClient")
def test_try_on_freepik_generation_failed(mock_httpx_cls, mock_settings, mock_sleep, client):
    """Should return error when Freepik reports FAILED status."""
    mock_client = AsyncMock()
    mock_httpx_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_httpx_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_shopify_resp = MagicMock()
    mock_shopify_resp.json.return_value = {"products": [{
        "title": "Diamond Tennis Bracelet",
        "images": [],
    }]}

    mock_freepik_submit = MagicMock()
    mock_freepik_submit.status_code = 200
    mock_freepik_submit.json.return_value = {
        "data": {"task_id": "fail-task", "status": "CREATED", "generated": []}
    }

    mock_freepik_poll = MagicMock()
    mock_freepik_poll.json.return_value = {
        "data": {"task_id": "fail-task", "status": "FAILED", "generated": []}
    }

    mock_client.get.side_effect = [mock_shopify_resp, mock_freepik_poll]
    mock_client.post.return_value = mock_freepik_submit

    mock_settings.shopify_shop_domain = "test.myshopify.com"
    mock_settings.shopify_access_token = "test-token"
    mock_settings.freepik_api_key = "test-freepik-key"

    b64 = _make_test_image_b64()
    response = client.post("/api/try-on", json={
        "wrist_image": b64,
        "product_handle": "test-bracelet",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "failed" in data["message"].lower()


def test_try_on_data_uri_prefix_stripped(client):
    """Should handle data:image/jpeg;base64, prefix correctly."""
    b64 = _make_test_image_b64()
    data_uri = f"data:image/jpeg;base64,{b64}"

    # This will fail at Shopify lookup (no mock) but should NOT fail at base64 parsing
    response = client.post("/api/try-on", json={
        "wrist_image": data_uri,
        "product_handle": "test-bracelet",
    })
    # Should get past validation (200 with error, not 400)
    assert response.status_code == 200
    data = response.json()
    # It'll fail at Shopify lookup since we didn't mock it, but that's fine —
    # the point is it didn't fail at base64 decoding
    assert data["status"] == "error"


def test_generate_wrist_mask():
    """Mask should be valid base64 PNG with correct dimensions."""
    import base64, io
    from PIL import Image
    from src.api.app import _generate_wrist_mask

    mask_b64 = _generate_wrist_mask(200, 400)
    raw = base64.b64decode(mask_b64)
    img = Image.open(io.BytesIO(raw))
    assert img.size == (200, 400)

    # Check that center band is white (pixel at center)
    px = img.getpixel((100, 200))
    assert px == (255, 255, 255)

    # Check that top is black
    px_top = img.getpixel((100, 10))
    assert px_top == (0, 0, 0)

    # Check that bottom is black
    px_bottom = img.getpixel((100, 390))
    assert px_bottom == (0, 0, 0)
