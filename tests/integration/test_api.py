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

@patch("src.api.app.Database")
def test_cron_daily_stats(mock_db_cls, client, cron_headers):
    """Daily stats cron should aggregate yesterday's orders."""
    mock_db = MagicMock()
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

@patch("src.api.app.ShopifyClient")
@patch("src.api.app.Database")
def test_cron_reconcile_orders(mock_db_cls, mock_shopify_cls, client, cron_headers):
    """Reconcile cron should process orders missed by webhooks."""
    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_orders.return_value = [
        {"id": 5001, "customer": {}, "email": "buyer@example.com",
         "total_price": "2850.00", "line_items": []},
    ]
    mock_shopify.close = AsyncMock()

    mock_db = MagicMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = None  # Not yet processed

    with patch("src.api.shopify_webhooks._process_order", new_callable=AsyncMock) as mock_process:
        response = client.post("/cron/reconcile-orders", headers=cron_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["reconciled"] == 1
        mock_process.assert_called_once()


@patch("src.api.app.ShopifyClient")
@patch("src.api.app.Database")
def test_cron_reconcile_skips_existing(mock_db_cls, mock_shopify_cls, client, cron_headers):
    """Reconcile should skip orders already in the database."""
    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_orders.return_value = [
        {"id": 5002, "customer": {}, "email": "buyer@example.com"},
    ]
    mock_shopify.close = AsyncMock()

    mock_db = MagicMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_order_by_shopify_id.return_value = {"shopify_order_id": 5002}

    with patch("src.api.shopify_webhooks._process_order", new_callable=AsyncMock) as mock_process:
        response = client.post("/cron/reconcile-orders", headers=cron_headers)
        assert response.status_code == 200
        assert response.json()["reconciled"] == 0
        mock_process.assert_not_called()


# ── Cron: Crafting Updates ──

@patch("src.api.app.MessageClassifier")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.Database")
def test_cron_crafting_updates(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Crafting update cron should send Slack reviews for eligible orders."""
    mock_db = MagicMock()
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
@patch("src.api.app.Database")
def test_cron_abandoned_carts(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Abandoned cart cron should flag carts for Slack review."""
    mock_db = MagicMock()
    mock_db_cls.return_value = mock_db
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
@patch("src.api.app.Database")
def test_cron_abandoned_carts_skip_low_value(mock_db_cls, mock_slack_cls, mock_class_cls, client, cron_headers):
    """Abandoned carts under $1 should be skipped."""
    mock_db = MagicMock()
    mock_db_cls.return_value = mock_db
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


# ── Cron: Morning Digest ──

@patch("src.api.app.ShopifyClient")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.Database")
def test_cron_morning_digest(mock_db_cls, mock_slack_cls, mock_shopify_cls, client, cron_headers):
    """Morning digest should send revenue, orders, and customer counts."""
    mock_db = MagicMock()
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
    ]

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/morning-digest", headers=cron_headers)
    assert response.status_code == 200
    mock_slack.send_blocks.assert_called_once()
    mock_shopify.get_webhooks.assert_called_once()


@patch("src.api.app.ShopifyClient")
@patch("src.api.app.SlackNotifier")
@patch("src.api.app.Database")
def test_cron_morning_digest_missing_webhooks(mock_db_cls, mock_slack_cls, mock_shopify_cls, client, cron_headers):
    """Morning digest should alert when webhook subscriptions are missing."""
    mock_db = MagicMock()
    mock_db_cls.return_value = mock_db
    mock_db.get_stats_range.return_value = [{"revenue": 0, "order_count": 0, "new_customers": 0}]
    mock_db.get_pending_messages.return_value = []
    mock_db.get_abandoned_carts_pending_recovery.return_value = []

    mock_shopify = AsyncMock()
    mock_shopify_cls.return_value = mock_shopify
    mock_shopify.get_webhooks.return_value = [
        {"topic": "orders/create"},
    ]  # Missing customers/create and checkouts/create

    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    response = client.post("/cron/morning-digest", headers=cron_headers)
    assert response.status_code == 200

    blocks = mock_slack.send_blocks.call_args[0][0]
    warning_texts = [
        b["text"]["text"] for b in blocks
        if b["type"] == "section" and "missing" in b.get("text", {}).get("text", "").lower()
    ]
    assert len(warning_texts) == 1
    assert "checkouts/create" in warning_texts[0]
    assert "customers/create" in warning_texts[0]


# ── Cron: Weekly Rollup ──

@patch("src.api.app.SlackNotifier")
@patch("src.api.app.Database")
def test_cron_weekly_rollup(mock_db_cls, mock_slack_cls, client, cron_headers):
    """Weekly rollup should aggregate 7 days of stats."""
    mock_db = MagicMock()
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
@patch("src.api.app.Database")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_approve_response(mock_slack_cls, mock_db_cls, mock_email_cls, client):
    """Approve action should send via SendGrid and tombstone the message."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
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
@patch("src.api.app.Database")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_approve_cart_recovery(mock_slack_cls, mock_db_cls, mock_email_cls, client):
    """Approve cart recovery should send email and update cart status."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
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

@patch("src.api.app.Database")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_skip_cart_recovery(mock_slack_cls, mock_db_cls, client):
    """Skip cart recovery should cancel the recovery status."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
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
@patch("src.api.app.Database")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_approve_crafting_update(mock_slack_cls, mock_db_cls, mock_email_cls, client):
    """Approve crafting update should send email and update order status."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
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
@patch("src.api.app.Database")
def test_slack_webhook_approve_shipment(mock_db_cls, mock_slack_cls, client):
    """Approve shipment should update order status and tombstone."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
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
@patch("src.api.app.Database")
def test_slack_webhook_hold_order(mock_db_cls, mock_slack_cls, client):
    """Hold order should update status to held_for_review."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
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

@patch("src.api.app.Database")
@patch("src.api.app.SlackNotifier")
def test_slack_webhook_reject_response(mock_slack_cls, mock_db_cls, client):
    """Reject should update message status to rejected."""
    mock_slack = AsyncMock()
    mock_slack_cls.return_value = mock_slack

    mock_db = MagicMock()
    mock_db_cls.return_value = mock_db

    payload = json.dumps({
        "actions": [{"action_id": "reject_response", "value": "1"}],
        "channel": {"id": "C123"},
        "message": {"ts": "123.456"},
    })

    response = client.post("/webhook/slack", data={"payload": payload})
    assert response.status_code == 200
    mock_db.update_message_status.assert_called_once_with(1, "rejected")
