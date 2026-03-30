"""Shopify webhook handlers with HMAC verification.

All webhooks return 200 BEFORE Supabase persist (Shopify 5-second timeout).
Heavy processing runs in FastAPI BackgroundTasks. Reconciliation cron catches
anything lost.
"""

import hashlib
import hmac
import base64
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request

from src.core.database import Database
from src.core.events import event_bus
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)

# Lazy singletons — avoid connecting at import time
_db = None
_slack = None


def _get_db():
    global _db
    if _db is None:
        _db = Database()
    return _db


def _get_slack():
    global _slack
    if _slack is None:
        _slack = SlackNotifier()
    return _slack


def verify_shopify_hmac(body: bytes, hmac_header: str) -> bool:
    """Verify Shopify webhook HMAC-SHA256 signature."""
    if not settings.shopify_webhook_secret:
        logger.warning("No Shopify webhook secret configured, skipping HMAC verification")
        return True

    digest = hmac.new(
        settings.shopify_webhook_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header)


async def validate_shopify_request(request: Request) -> tuple[bytes, dict[str, Any]]:
    """Read body, verify HMAC, parse JSON. Returns (raw_body, parsed_data)."""
    body = await request.body()
    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256", "")

    if not hmac_header:
        raise HTTPException(status_code=401, detail="Missing HMAC header")

    if not verify_shopify_hmac(body, hmac_header):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    return body, data


# ── Background task handlers ──

async def _process_order(order_data: dict[str, Any]) -> None:
    """Process a Shopify order: persist, upsert customer, check fraud, emit events."""
    shopify_order_id = order_data.get("id")

    # Idempotency check
    existing = _get_db().get_order_by_shopify_id(shopify_order_id)
    if existing:
        logger.info("Order %s already processed, skipping", shopify_order_id)
        return

    # Extract customer info
    customer_data = order_data.get("customer", {})
    shopify_customer_id = customer_data.get("id")
    customer_email = order_data.get("email", customer_data.get("email", ""))
    customer_name = f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()

    # Upsert customer
    customer_record = None
    if shopify_customer_id:
        customer_record = _get_db().upsert_customer({
            "shopify_customer_id": shopify_customer_id,
            "email": customer_email,
            "name": customer_name or customer_email,
            "phone": customer_data.get("phone", ""),
            "accepts_marketing": customer_data.get("accepts_marketing", False),
            "order_count": customer_data.get("orders_count", 1),
            "lifetime_value": float(customer_data.get("total_spent", "0")),
            "first_order_date": datetime.utcnow().isoformat(),
            "last_order_date": datetime.utcnow().isoformat(),
        })

        # Update lifecycle stage
        orders_count = customer_data.get("orders_count", 1)
        if orders_count >= 3:
            stage = "advocate"
        elif orders_count >= 2:
            stage = "repeat"
        else:
            stage = "first_purchase"
        _get_db().update_customer_lifecycle(customer_record["id"], stage)

    # Persist order
    total = float(order_data.get("total_price", "0"))
    subtotal = float(order_data.get("subtotal_price", "0"))
    tax = float(order_data.get("total_tax", "0"))

    order_record = _get_db().upsert_order({
        "shopify_order_id": shopify_order_id,
        "customer_id": customer_record["id"] if customer_record else None,
        "buyer_email": customer_email,
        "buyer_name": customer_name,
        "total": total,
        "subtotal": subtotal,
        "tax": tax,
        "shipping_cost": float(order_data.get("total_shipping_price_set", {}).get("shop_money", {}).get("amount", "0")),
        "status": "paid",
        "checkout_token": order_data.get("checkout_token", ""),
        "created_at": order_data.get("created_at", datetime.utcnow().isoformat()),
    })

    # Cancel any pending abandoned cart recovery for this checkout
    checkout_token = order_data.get("checkout_token", "")
    if checkout_token:
        _get_db().cancel_cart_recovery(checkout_token)

    # Emit event for other handlers (fraud check, Slack alert, etc.)
    await event_bus.emit("order.created", {
        "order": order_record,
        "customer": customer_record,
        "shopify_data": order_data,
    })

    # Send Slack alert for new order
    await _get_slack().send_new_order_alert(
        order_number=str(order_data.get("order_number", shopify_order_id)),
        customer_name=customer_name or customer_email,
        total=total,
        items=[
            item.get("title", "Unknown")
            for item in order_data.get("line_items", [])
        ],
    )

    logger.info("Order #%s processed: $%.2f from %s", shopify_order_id, total, customer_name)


async def _process_customer(customer_data: dict[str, Any]) -> None:
    """Process a Shopify customer create/update event."""
    shopify_customer_id = customer_data.get("id")
    if not shopify_customer_id:
        return

    email = customer_data.get("email", "")
    name = f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()

    _get_db().upsert_customer({
        "shopify_customer_id": shopify_customer_id,
        "email": email,
        "name": name or email,
        "phone": customer_data.get("phone", ""),
        "accepts_marketing": customer_data.get("accepts_marketing", False),
    })

    await event_bus.emit("customer.created", {"customer_data": customer_data})
    logger.info("Customer %s upserted: %s", shopify_customer_id, name or email)


async def _process_checkout(checkout_data: dict[str, Any]) -> None:
    """Process a Shopify checkout create/update for abandoned cart tracking."""
    checkout_token = checkout_data.get("token", "")
    if not checkout_token:
        return

    customer_email = checkout_data.get("email", "")
    cart_value = float(checkout_data.get("total_price", "0"))
    items = [
        {"title": item.get("title", ""), "quantity": item.get("quantity", 1), "price": item.get("price", "0")}
        for item in checkout_data.get("line_items", [])
    ]

    # Look up customer
    customer_id = None
    if customer_email:
        customer = _get_db().get_customer_by_email(customer_email)
        if customer:
            customer_id = customer["id"]

    _get_db().upsert_cart_event({
        "shopify_checkout_token": checkout_token,
        "customer_id": customer_id,
        "customer_email": customer_email,
        "event_type": "created",
        "cart_value": cart_value,
        "items_json": json.dumps(items),
    })

    logger.info("Checkout %s tracked: $%.2f, %s", checkout_token, cart_value, customer_email or "anonymous")


# ── Route handlers (called from app.py) ──

async def handle_order_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle orders/create and orders/paid webhooks."""
    _, order_data = await validate_shopify_request(request)
    background_tasks.add_task(_process_order, order_data)
    return {"status": "received"}


async def handle_customer_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle customers/create and customers/update webhooks."""
    _, customer_data = await validate_shopify_request(request)
    background_tasks.add_task(_process_customer, customer_data)
    return {"status": "received"}


async def handle_checkout_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle checkouts/create and checkouts/update webhooks."""
    _, checkout_data = await validate_shopify_request(request)
    background_tasks.add_task(_process_checkout, checkout_data)
    return {"status": "received"}
