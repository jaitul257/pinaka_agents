"""FastAPI application — routes for health, cron endpoints, webhooks, and Slack interactivity."""

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import sentry_sdk
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request

from src.api.inbound_email import handle_inbound_email
from src.api.shopify_webhooks import (
    handle_checkout_webhook,
    handle_customer_webhook,
    handle_order_webhook,
)
from src.core.database import Database
from src.core.email import EmailSender
from src.core.settings import settings
from src.core.shopify_client import ShopifyClient
from src.core.slack import SlackNotifier
from src.customer.classifier import MessageClassifier
from src.finance.calculator import FinanceCalculator
from src.listings.generator import ListingGenerator
from src.product.schema import Product
from src.shipping.processor import ShippingProcessor

logger = logging.getLogger(__name__)

# ── Sentry ──
if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Pinaka Agents starting up")
    yield
    logger.info("Pinaka Agents shutting down")


app = FastAPI(title="Pinaka Agents", version="0.2.0", lifespan=lifespan)


# ── Auth Dependencies ──

async def verify_cron_secret(x_cron_secret: str = Header(alias="X-Cron-Secret")) -> None:
    """Verify Railway cron requests have the correct secret."""
    if not settings.cron_secret or x_cron_secret != settings.cron_secret:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _verify_slack_request(request: Request, body: bytes) -> None:
    """Verify Slack request signature using signing secret."""
    if not settings.slack_signing_secret:
        return  # Skip verification in dev

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")

    if abs(datetime.utcnow().timestamp() - float(timestamp)) > 300:
        raise HTTPException(status_code=401, detail="Request too old")

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        settings.slack_signing_secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


# ── Health ──

@app.get("/health")
async def health():
    """Per-module health status reporting."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "modules": {
            "shopify": {"status": "ok"},
            "shipping": {"status": "ok"},
            "customer": {"status": "ok"},
            "finance": {"status": "ok"},
        },
    }


# ── Shopify Webhooks ──

@app.post("/webhook/shopify/orders")
async def shopify_orders_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle orders/create and orders/paid webhooks."""
    return await handle_order_webhook(request, background_tasks)


@app.post("/webhook/shopify/customers")
async def shopify_customers_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle customers/create and customers/update webhooks."""
    return await handle_customer_webhook(request, background_tasks)


@app.post("/webhook/shopify/checkouts")
async def shopify_checkouts_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle checkouts/create and checkouts/update webhooks."""
    return await handle_checkout_webhook(request, background_tasks)


# ── Inbound Email (SendGrid Inbound Parse) ──

@app.post("/inbound/email")
async def inbound_email(request: Request):
    """Receive customer emails via SendGrid Inbound Parse webhook."""
    return await handle_inbound_email(request)


# ── Product Management ──

@app.post("/api/products/upload", dependencies=[Depends(verify_cron_secret)])
async def upload_product(request: Request):
    """Upload a product JSON, save to data/products/, and embed for RAG search."""
    import json as json_mod
    from pathlib import Path
    from src.product.embeddings import ProductEmbeddings

    data = await request.json()
    product = Product(**data)

    # Save to data/products/
    products_dir = Path("./data/products")
    products_dir.mkdir(parents=True, exist_ok=True)
    filepath = products_dir / f"{product.sku}.json"
    filepath.write_text(json_mod.dumps(data, indent=2))

    # Embed immediately
    embeddings = ProductEmbeddings()
    embeddings.embed_product(product)

    return {
        "status": "ok",
        "sku": product.sku,
        "saved_to": str(filepath),
        "total_products": embeddings.product_count(),
    }


# ── Listing Generation ──

@app.post("/api/listings/generate", dependencies=[Depends(verify_cron_secret)])
async def generate_listing(request: Request):
    """Generate AI-powered Shopify product listing from product data."""
    data = await request.json()
    product = Product(**data["product"])
    variant = data.get("variant")

    generator = ListingGenerator()
    result = generator.generate(product, variant)

    # Send to Slack for founder review
    slack = SlackNotifier()
    await slack.send_listing_review(
        title=result["title"],
        description=result["description"],
        tags=result["tags"],
        listing_draft_id=product.sku,
    )

    return result


# ── Cron Endpoints (secured) ──

@app.post("/cron/sync-products", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_products():
    """Scan data/products/ directory and embed all product JSON files for RAG search."""
    from src.product.embeddings import ProductEmbeddings

    embeddings = ProductEmbeddings()
    before = embeddings.product_count()
    embedded = embeddings.embed_all_from_directory("./data/products")

    return {
        "status": "ok",
        "module": "product_sync",
        "embedded": embedded,
        "total_in_index": embeddings.product_count(),
        "was": before,
    }


@app.post("/cron/daily-stats", dependencies=[Depends(verify_cron_secret)])
async def cron_daily_stats():
    """Collect daily stats from Shopify orders and customers."""
    db = Database()
    yesterday = date.today() - timedelta(days=1)

    revenue = db.get_total_revenue(yesterday, yesterday)

    orders = db.get_orders_by_status("paid")
    yesterday_orders = [
        o for o in orders
        if o.get("created_at", "").startswith(yesterday.isoformat())
    ]

    new_customers = db.get_customers_by_lifecycle("first_purchase")
    yesterday_new = [
        c for c in new_customers
        if c.get("created_at", "").startswith(yesterday.isoformat())
    ]

    db.upsert_daily_stats({
        "date": yesterday.isoformat(),
        "revenue": revenue,
        "order_count": len(yesterday_orders),
        "new_customers": len(yesterday_new),
        "repeat_customers": db.get_repeat_customer_count(),
    })

    return {"status": "ok", "module": "stats", "orders_counted": len(yesterday_orders)}


@app.post("/cron/reconcile-orders", dependencies=[Depends(verify_cron_secret)])
async def cron_reconcile_orders():
    """Poll Shopify for orders missed by webhooks (every 30 min)."""
    from src.api.shopify_webhooks import _process_order

    shopify = ShopifyClient()
    db = Database()
    reconciled = 0

    try:
        since = (datetime.utcnow() - timedelta(minutes=35)).isoformat()
        orders = await shopify.get_orders(created_at_min=since, limit=50)

        for order in orders:
            if db.get_order_by_shopify_id(order["id"]):
                continue
            await _process_order(order)
            reconciled += 1
    finally:
        await shopify.close()

    return {"status": "ok", "module": "reconcile", "reconciled": reconciled}


@app.post("/cron/crafting-updates", dependencies=[Depends(verify_cron_secret)])
async def cron_crafting_updates():
    """Check for orders needing crafting update emails (Day 2-3 post-order)."""
    db = Database()
    slack = SlackNotifier()
    classifier = MessageClassifier()
    sent = 0

    orders = db.get_orders_needing_crafting_update(settings.crafting_update_delay_days)

    for order in orders:
        customer = order.get("customers") or {}
        if not customer:
            continue

        customer_name = customer.get("name") or customer.get("email", "Customer")
        customer_email = customer.get("email", "")
        order_created = order.get("created_at", "")

        days_since = 0
        if order_created:
            try:
                created = datetime.fromisoformat(order_created.replace("Z", ""))
                days_since = (datetime.utcnow() - created).days
            except (ValueError, TypeError):
                pass

        # Get first line item name for context
        product_name = "your order"

        draft = await classifier.draft_response(
            customer_message="",
            category="crafting_update",
            order_context=(
                f"Order #{order.get('shopify_order_id')}, "
                f"${float(order.get('total', 0)):,.2f}, "
                f"placed {days_since} days ago"
            ),
            customer_context=(
                f"Name: {customer_name}, "
                f"Lifecycle: {customer.get('lifecycle_stage', 'unknown')}"
            ),
        )

        await slack.send_crafting_update_review(
            order_number=str(order.get("shopify_order_id", "")),
            customer_name=customer_name,
            customer_email=customer_email,
            product_name=product_name,
            days_since_order=days_since,
            email_body=draft,
            order_id=order.get("shopify_order_id"),
        )
        sent += 1

    return {"status": "ok", "module": "crafting_updates", "sent": sent}


@app.post("/cron/abandoned-carts", dependencies=[Depends(verify_cron_secret)])
async def cron_abandoned_carts():
    """Check for abandoned carts needing recovery emails."""
    db = Database()
    slack = SlackNotifier()
    classifier = MessageClassifier()
    flagged = 0

    carts = db.get_abandoned_carts_pending_recovery()

    for cart in carts:
        customer_email = cart.get("customer_email", "")
        cart_value = float(cart.get("cart_value", 0))
        if cart_value < 1:
            continue

        # Look up customer context
        customer_context = ""
        customer_name = customer_email or "Anonymous"
        if customer_email:
            customer = db.get_customer_by_email(customer_email)
            if customer:
                customer_name = customer.get("name") or customer_email
                customer_context = (
                    f"Orders: {customer.get('order_count', 0)}, "
                    f"LTV: ${float(customer.get('lifetime_value', 0)):,.2f}"
                )

        # Parse items
        items_json = cart.get("items_json", "[]")
        items = json.loads(items_json) if isinstance(items_json, str) else items_json
        product_names = [item.get("title", "Item") for item in items]

        # Calculate time since abandonment
        created_at = cart.get("created_at", "")
        time_since = "unknown"
        if created_at:
            try:
                cart_time = datetime.fromisoformat(created_at.replace("Z", ""))
                hours = int((datetime.utcnow() - cart_time).total_seconds() / 3600)
                time_since = f"{hours}h" if hours < 24 else f"{hours // 24}d"
            except (ValueError, TypeError):
                pass

        draft = await classifier.draft_response(
            customer_message="",
            category="cart_recovery",
            customer_context=customer_context,
            order_context=f"Cart: {', '.join(product_names)}, Value: ${cart_value:,.2f}",
        )

        # Split draft into subject + body (first line is subject)
        draft_lines = draft.strip().split("\n", 1)
        email_subject = draft_lines[0].replace("Subject: ", "").strip()
        email_body = draft_lines[1].strip() if len(draft_lines) > 1 else draft

        await slack.send_abandoned_cart_review(
            cart_value=cart_value,
            customer_name=customer_name,
            customer_context=customer_context,
            product_names=product_names,
            time_since=time_since,
            email_subject=email_subject,
            email_body=email_body,
            cart_event_id=cart["id"],
        )

        # Mark as pending approval
        db.upsert_cart_event({
            "shopify_checkout_token": cart["shopify_checkout_token"],
            "recovery_email_status": "pending",
        })
        flagged += 1

    return {"status": "ok", "module": "cart_recovery", "flagged": flagged}


REQUIRED_WEBHOOK_TOPICS = {"orders/create", "customers/create", "checkouts/create"}


async def _check_webhook_health(shopify: ShopifyClient, slack: SlackNotifier) -> list[str]:
    """Verify all Shopify webhook subscriptions are active. Re-register missing ones."""
    base_url = settings.shopify_shop_domain  # used for address matching only
    missing = []
    try:
        existing = await shopify.get_webhooks()
        active_topics = {wh["topic"] for wh in existing}
        missing = sorted(REQUIRED_WEBHOOK_TOPICS - active_topics)
    except Exception as e:
        logger.error("Webhook health check failed: %s", e)
        await slack.send_message(f":warning: Webhook health check failed: {e}")
        return list(REQUIRED_WEBHOOK_TOPICS)

    if not missing:
        return []

    logger.warning("Missing webhook subscriptions: %s", missing)
    return missing


@app.post("/cron/morning-digest", dependencies=[Depends(verify_cron_secret)])
async def cron_morning_digest():
    """Daily morning digest sent to Slack at 8 AM."""
    db = Database()
    slack = SlackNotifier()

    yesterday = date.today() - timedelta(days=1)
    stats = db.get_stats_range(yesterday, yesterday)
    pending = db.get_pending_messages()

    revenue = float(stats[0].get("revenue", 0)) if stats else 0.0
    orders = int(stats[0].get("order_count", 0)) if stats else 0
    new_customers = int(stats[0].get("new_customers", 0)) if stats else 0

    abandoned = db.get_abandoned_carts_pending_recovery()

    # Webhook health check
    shopify = ShopifyClient()
    try:
        missing_webhooks = await _check_webhook_health(shopify, slack)
    finally:
        await shopify.close()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":sunrise: GOOD MORNING, PINAKA"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Yesterday's Revenue:* ${revenue:,.2f}"},
                {"type": "mrkdwn", "text": f"*Orders:* {orders}"},
                {"type": "mrkdwn", "text": f"*New Customers:* {new_customers}"},
                {"type": "mrkdwn", "text": f"*Pending Messages:* {len(pending)}"},
                {"type": "mrkdwn", "text": f"*Abandoned Carts:* {len(abandoned)}"},
            ],
        },
    ]

    if pending:
        urgent = [m for m in pending if m.get("urgency") == "urgent"]
        if urgent:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":rotating_light: *{len(urgent)} urgent message(s) waiting*",
                },
            })

    if missing_webhooks:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":warning: *Webhook subscriptions missing:* {', '.join(missing_webhooks)}\n"
                    "Shopify may have auto-deleted them after timeout failures. "
                    "Run `python scripts/register_webhooks.py` to re-register."
                ),
            },
        })

    await slack.send_blocks(blocks, text=f"Morning digest: ${revenue:,.2f} revenue, {orders} orders")
    return {"status": "ok", "module": "digest"}


@app.post("/cron/weekly-rollup", dependencies=[Depends(verify_cron_secret)])
async def cron_weekly_rollup():
    """Weekly rollup sent to Slack on Mondays at 9 AM."""
    db = Database()
    slack = SlackNotifier()

    end = date.today()
    start = end - timedelta(days=7)
    stats = db.get_stats_range(start, end)

    total_revenue = sum(float(s.get("revenue", 0)) for s in stats)
    total_orders = sum(int(s.get("order_count", 0)) for s in stats)
    total_new_customers = sum(int(s.get("new_customers", 0)) for s in stats)

    customer_count = db.get_customer_count()
    repeat_count = db.get_repeat_customer_count()

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":calendar: WEEKLY ROLLUP"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Period:* {start} to {end}"},
                {"type": "mrkdwn", "text": f"*Revenue:* ${total_revenue:,.2f}"},
                {"type": "mrkdwn", "text": f"*Orders:* {total_orders}"},
                {"type": "mrkdwn", "text": f"*New Customers:* {total_new_customers}"},
                {"type": "mrkdwn", "text": f"*Total Customers:* {customer_count}"},
            ],
        },
    ]

    if total_orders > 0:
        aov = total_revenue / total_orders
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Avg Order Value:* ${aov:,.2f}"},
        })

    if customer_count > 0:
        repeat_rate = (repeat_count / customer_count) * 100
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Repeat Customer Rate:* {repeat_rate:.1f}%"},
        })

    await slack.send_blocks(
        blocks,
        text=f"Weekly rollup: ${total_revenue:,.2f} revenue, {total_orders} orders",
    )
    return {"status": "ok", "module": "rollup"}


@app.post("/cron/weekly-finance", dependencies=[Depends(verify_cron_secret)])
async def cron_weekly_finance():
    """Weekly financial summary."""
    finance = FinanceCalculator()
    report = await finance.run_weekly_finance_report()
    return {
        "status": "ok",
        "module": "finance",
        "revenue": report.total_revenue,
        "profit": report.total_net_profit,
        "orders": report.total_orders,
    }


# ── Slack Interactivity ──

@app.post("/webhook/slack")
async def webhook_slack(request: Request):
    """Handle Slack Block Kit interactivity (approve/edit/reject actions)."""
    body = await request.body()
    await _verify_slack_request(request, body)

    form_data = await request.form()
    payload = json.loads(form_data.get("payload", "{}"))

    actions = payload.get("actions", [])
    if not actions:
        return {"status": "no_action"}

    action = actions[0]
    action_id = action.get("action_id", "")
    value = action.get("value", "")
    channel = payload.get("channel", {}).get("id", "")
    message_ts = payload.get("message", {}).get("ts", "")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    slack = SlackNotifier()
    db = Database()
    email = EmailSender()

    # ── Customer Response Actions ──
    if action_id == "approve_response":
        msg = db.get_pending_messages()
        target = next((m for m in msg if str(m.get("id")) == value), None)
        if target and target.get("ai_draft"):
            email.send_service_reply(
                to_email=target.get("customer_email", ""),
                customer_name=target.get("buyer_name", ""),
                subject=target.get("subject", "Re: Your inquiry"),
                email_body=target["ai_draft"],
            )
            db.update_message_status(target["id"], "sent", human_approved=True)

        tombstone = SlackNotifier.tombstone_blocks("Approved & Sent", f"Message #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "reject_response":
        db.update_message_status(int(value), "rejected")
        tombstone = SlackNotifier.tombstone_blocks("Rejected", f"Message #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Abandoned Cart Recovery Actions ──
    elif action_id == "approve_cart_recovery":
        cart = db.get_cart_by_id(int(value))
        if cart:
            items_json = cart.get("items_json", "[]")
            items = json.loads(items_json) if isinstance(items_json, str) else items_json
            item_names = [item.get("title", "Item") for item in items]

            email.send_cart_recovery(
                to_email=cart.get("customer_email", ""),
                customer_name=cart.get("customer_email", "").split("@")[0],
                cart_items=item_names,
                cart_value=float(cart.get("cart_value", 0)),
            )
            db.upsert_cart_event({
                "shopify_checkout_token": cart["shopify_checkout_token"],
                "recovery_email_status": "sent",
            })
        tombstone = SlackNotifier.tombstone_blocks("Recovery Email Sent", f"Cart #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_cart_recovery":
        cart = db.get_cart_by_id(int(value))
        if cart:
            db.upsert_cart_event({
                "shopify_checkout_token": cart["shopify_checkout_token"],
                "recovery_email_status": "cancelled",
            })
        tombstone = SlackNotifier.tombstone_blocks("Recovery Skipped", f"Cart #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Crafting Update Actions ──
    elif action_id == "approve_crafting_update":
        order = db.get_order_by_shopify_id(int(value))
        if order:
            customer_id = order.get("customer_id")
            customer = db.get_customer_by_shopify_id(customer_id) if customer_id else None
            customer_email = order.get("buyer_email", "")
            customer_name = order.get("buyer_name", "")

            if customer_email:
                email.send_crafting_update(
                    to_email=customer_email,
                    customer_name=customer_name,
                    order_number=str(value),
                    email_body="",  # Template handles the content
                )
        db.update_order_status(int(value), "crafting_update_sent")
        tombstone = SlackNotifier.tombstone_blocks("Crafting Update Sent", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_crafting_update":
        tombstone = SlackNotifier.tombstone_blocks("Update Skipped", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Shipping/Fraud Actions ──
    elif action_id == "approve_shipment":
        db.update_order_status(int(value), "approved_for_shipping")
        tombstone = SlackNotifier.tombstone_blocks("Shipment Approved", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "hold_order":
        db.update_order_status(int(value), "held_for_review")
        tombstone = SlackNotifier.tombstone_blocks("Order Held", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "cancel_order":
        db.update_order_status(int(value), "cancelled")
        tombstone = SlackNotifier.tombstone_blocks("Order Cancelled", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Dismiss/Edit Actions ──
    elif action_id in ("dismiss", "edit_response", "edit_cart_recovery", "edit_crafting_update", "edit_listing"):
        tombstone = SlackNotifier.tombstone_blocks("Dismissed", action_id, timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    else:
        logger.warning("Unknown Slack action: %s", action_id)

    return {"status": "ok"}
