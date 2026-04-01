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

from src.core.attribution import extract_attribution
from src.core.database import AsyncDatabase, Database
from src.core.email import EmailSender
from src.core.events import event_bus
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.shipping.processor import ShippingProcessor

logger = logging.getLogger(__name__)

# Lazy singletons — avoid connecting at import time
_db = None
_async_db = None
_slack = None
_email = None
_shipping = None


def _get_db():
    """Sync Database for non-async callers (e.g. ShippingProcessor internals)."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def _get_async_db():
    """AsyncDatabase for webhook handlers — non-blocking DB calls."""
    global _async_db
    if _async_db is None:
        _async_db = AsyncDatabase()
    return _async_db


def _get_slack():
    global _slack
    if _slack is None:
        _slack = SlackNotifier()
    return _slack


def _get_email():
    global _email
    if _email is None:
        _email = EmailSender()
    return _email


def _get_shipping():
    global _shipping
    if _shipping is None:
        _shipping = ShippingProcessor()
    return _shipping



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
    try:
        await _process_order_inner(order_data)
    except Exception:
        logger.exception("Failed to process order %s", order_data.get("id"))


async def _process_order_inner(order_data: dict[str, Any]) -> None:
    shopify_order_id = order_data.get("id")
    db = _get_async_db()

    # Idempotency check
    existing = await db.get_order_by_shopify_id(shopify_order_id)
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
        customer_record = await db.upsert_customer({
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
        await db.update_customer_lifecycle(customer_record["id"], stage)

    # Persist order with attribution params
    total = float(order_data.get("total_price", "0"))
    subtotal = float(order_data.get("subtotal_price", "0"))
    tax = float(order_data.get("total_tax", "0"))
    attribution = extract_attribution(order_data)

    order_record = await db.upsert_order({
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
        **{k: v for k, v in attribution.items() if v is not None},
    })

    # Cancel any pending abandoned cart recovery for this checkout
    checkout_token = order_data.get("checkout_token", "")
    if checkout_token:
        await db.cancel_cart_recovery(checkout_token)

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

    # Run fraud check
    fraud_result = _get_shipping().check_fraud(order_record)
    if fraud_result.is_flagged:
        insurance_note = ""
        if fraud_result.insurance_gap > 0:
            insurance_note = (
                f"Carrier cap: ${settings.carrier_insurance_cap:,.2f}. "
                f"Gap: ${fraud_result.insurance_gap:,.2f}. Supplemental required."
            )
        await _get_slack().send_fraud_alert(
            receipt_id=shopify_order_id,
            buyer_name=customer_name or customer_email,
            total=total,
            flag_reason=" | ".join(fraud_result.reasons),
            insurance_note=insurance_note,
        )
        logger.warning("Order #%s flagged for fraud: %s", shopify_order_id, fraud_result.reasons)

    # Push to ShipStation (skip if flagged for fraud)
    if not fraud_result.is_flagged and settings.shipstation_api_key:
        try:
            ss_result = await _get_shipping().create_shipstation_order({
                **order_record,
                "shipping_address": order_data.get("shipping_address", {}),
                "billing_address": order_data.get("billing_address", {}),
                "line_items": order_data.get("line_items", []),
                "order_number": order_data.get("order_number", shopify_order_id),
            })
            if ss_result.get("orderId"):
                logger.info("Order #%s pushed to ShipStation (SS ID: %s)", shopify_order_id, ss_result["orderId"])
                # Store ShipStation order ID for tracking webhook correlation
                await db.update_order_status(
                    shopify_order_id, order_record.get("status", "paid"),
                    shipstation_order_id=ss_result["orderId"],
                )
            elif ss_result.get("error"):
                logger.error("ShipStation push failed for order #%s: %s", shopify_order_id, ss_result)
        except Exception:
            logger.exception("ShipStation push failed for order #%s", shopify_order_id)

    # Send order confirmation email
    if customer_email:
        shipping_addr = order_data.get("shipping_address", {})
        addr_parts = [
            shipping_addr.get("address1", ""),
            shipping_addr.get("city", ""),
            shipping_addr.get("province", ""),
            shipping_addr.get("zip", ""),
            shipping_addr.get("country", ""),
        ]
        address_str = ", ".join(p for p in addr_parts if p)

        _get_email().send_order_confirmation(
            to_email=customer_email,
            customer_name=customer_name or customer_email,
            order_number=str(order_data.get("order_number", shopify_order_id)),
            line_items=order_data.get("line_items", []),
            total=total,
            shipping_address=address_str,
        )

    # Fire Meta Conversions API purchase event (only for paid orders)
    if order_data.get("financial_status") == "paid":
        try:
            from src.marketing.meta_capi import MetaConversionsAPI
            meta = MetaConversionsAPI()
            if meta.is_configured:
                await meta.send_purchase_event(
                    order_data=order_data,
                    customer_email=customer_email,
                    customer_phone=customer_data.get("phone", ""),
                    customer_first_name=customer_data.get("first_name", ""),
                    customer_last_name=customer_data.get("last_name", ""),
                )
        except Exception:
            logger.exception("Meta CAPI failed for order #%s (non-blocking)", shopify_order_id)

        # Fire Google Ads offline conversion (only when gclid is present)
        if attribution.get("gclid"):
            try:
                from src.marketing.google_conversions import GoogleOfflineConversions
                google_conv = GoogleOfflineConversions()
                if google_conv.is_configured:
                    await google_conv.send_purchase_conversion(
                        gclid=attribution["gclid"],
                        conversion_date_time=order_data.get("created_at", ""),
                        conversion_value=total,
                        order_id=str(shopify_order_id),
                    )
            except Exception:
                logger.exception("Google conversion failed for order #%s (non-blocking)", shopify_order_id)

    logger.info("Order #%s processed: $%.2f from %s", shopify_order_id, total, customer_name)


async def _process_customer(customer_data: dict[str, Any]) -> None:
    """Process a Shopify customer create/update event."""
    shopify_customer_id = customer_data.get("id")
    if not shopify_customer_id:
        return

    email = customer_data.get("email", "")
    name = f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()

    await _get_async_db().upsert_customer({
        "shopify_customer_id": shopify_customer_id,
        "email": email,
        "name": name or email,
        "phone": customer_data.get("phone", ""),
        "accepts_marketing": customer_data.get("accepts_marketing", False),
    })

    await event_bus.emit("customer.created", {"customer_data": customer_data})
    logger.info("Customer %s upserted: %s", shopify_customer_id, name or email)


async def _process_refund(refund_data: dict[str, Any]) -> None:
    """Process a Shopify refunds/create webhook."""
    try:
        await _process_refund_inner(refund_data)
    except Exception:
        logger.exception("Failed to process refund %s", refund_data.get("id"))


async def _process_refund_inner(refund_data: dict[str, Any]) -> None:
    shopify_refund_id = refund_data.get("id")
    shopify_order_id = refund_data.get("order_id")

    if not shopify_refund_id or not shopify_order_id:
        logger.warning("Refund webhook missing id or order_id, skipping")
        return

    db = _get_async_db()

    # Idempotency: skip if we already processed this refund
    if await db.get_refund_by_shopify_id(shopify_refund_id):
        logger.info("Refund %s already processed, skipping", shopify_refund_id)
        return

    # Look up the order
    order = await db.get_order_by_shopify_id(shopify_order_id)
    if not order:
        # Race condition: refund arrived before order. Retry with backoff.
        import asyncio
        for attempt in range(3):
            await asyncio.sleep(30)
            order = await db.get_order_by_shopify_id(shopify_order_id)
            if order:
                break
        if not order:
            logger.error("Order %s not found after 3 retries, cannot process refund %s", shopify_order_id, shopify_refund_id)
            return

    # Calculate total refund amount from this webhook (filter for successful refund transactions)
    transactions = refund_data.get("transactions", [])
    refund_transactions = [
        t for t in transactions
        if t.get("kind") == "refund" and t.get("status") == "success"
    ]
    refund_amount = sum(float(t.get("amount", 0)) for t in refund_transactions)
    if refund_amount <= 0:
        # Fallback: sum refund line items
        refund_line_items = refund_data.get("refund_line_items", [])
        refund_amount = sum(
            float(li.get("subtotal", 0)) for li in refund_line_items
        )

    if refund_amount <= 0:
        logger.warning("Refund %s has zero amount, skipping", shopify_refund_id)
        return

    order_total = float(order.get("total", 0))
    current_refund_amount = float(order.get("refund_amount") or 0)
    is_partial = (current_refund_amount + refund_amount) < order_total

    reason = refund_data.get("note", "") or ""

    # Create refund record (also updates order.refund_amount)
    await db.create_refund({
        "order_id": order["id"],
        "shopify_refund_id": shopify_refund_id,
        "amount": refund_amount,
        "reason": reason,
        "is_partial": is_partial,
    })

    # Send Slack alert
    await _get_slack().send_refund_alert(
        order_number=str(shopify_order_id),
        refund_amount=refund_amount,
        reason=reason,
        is_partial=is_partial,
    )

    # Send refund confirmation email
    customer_email = order.get("buyer_email", "")
    customer_name = order.get("buyer_name", "")
    if customer_email:
        _get_email().send_refund_confirmation(
            to_email=customer_email,
            customer_name=customer_name or customer_email,
            order_number=str(shopify_order_id),
            refund_amount=refund_amount,
            is_partial=is_partial,
        )

    logger.info(
        "Refund #%s processed: $%.2f for order #%s (%s)",
        shopify_refund_id, refund_amount, shopify_order_id,
        "partial" if is_partial else "full",
    )


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
    db = _get_async_db()
    customer_id = None
    if customer_email:
        customer = await db.get_customer_by_email(customer_email)
        if customer:
            customer_id = customer["id"]

    await db.upsert_cart_event({
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


async def handle_refund_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle refunds/create webhooks."""
    _, refund_data = await validate_shopify_request(request)
    background_tasks.add_task(_process_refund, refund_data)
    return {"status": "received"}


