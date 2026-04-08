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
from src.dashboard.web import router as dashboard_router
from src.api.shopify_webhooks import (
    handle_checkout_webhook,
    handle_customer_webhook,
    handle_order_webhook,
    handle_refund_webhook,
)
from src.core.database import AsyncDatabase
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

    # Rebuild ChromaDB embeddings from Supabase so RAG works immediately
    try:
        from src.product.embeddings import ProductEmbeddings

        db = AsyncDatabase()
        products = await db.get_all_products()
        if products:
            embeddings = ProductEmbeddings()
            count = 0
            for p in products:
                try:
                    product_obj = Product(
                        sku=p["sku"],
                        name=p["name"],
                        category=p.get("category", ""),
                        materials=p.get("materials", {}),
                        pricing=p.get("pricing", {}),
                        story=p.get("story", ""),
                        care_instructions=p.get("care_instructions", ""),
                        occasions=p.get("occasions", []),
                        certification=p.get("certification"),
                        tags=p.get("tags", []),
                    )
                    embeddings.embed_product(product_obj)
                    count += 1
                except Exception:
                    logger.exception("Failed to embed product %s on startup", p.get("sku"))
            logger.info("Startup: embedded %d products in ChromaDB", count)
        else:
            logger.info("Startup: no products in Supabase to embed")
    except Exception:
        logger.exception("Startup: ChromaDB rebuild failed (non-fatal)")

    yield
    logger.info("Pinaka Agents shutting down")


app = FastAPI(title="Pinaka Agents", version="0.3.0", lifespan=lifespan)

# CORS for storefront chat widget (pinakajewellery.com → Railway API)
from starlette.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pinakajewellery.com",
        "https://www.pinakajewellery.com",
        "https://pinaka-jewellery.myshopify.com",
    ],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

app.include_router(dashboard_router)


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


# ── Shopify OAuth (for app install flow) ──

@app.get("/")
async def shopify_app_root(request: Request):
    """Handle Shopify app install redirect — start OAuth flow."""
    shop = request.query_params.get("shop", settings.shopify_shop_domain)
    if not shop:
        return {"status": "Pinaka Agents API", "docs": "/docs"}

    # Redirect to Shopify OAuth authorize
    scopes = "read_checkouts,read_customers,read_orders,read_products,read_shipping,write_customers,write_orders,write_products,write_shipping"
    redirect_uri = f"https://pinaka-agents-production-198b5.up.railway.app/api/auth"
    nonce = hashlib.sha256(f"{shop}{datetime.utcnow().isoformat()}".encode()).hexdigest()[:16]

    auth_url = (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={settings.shopify_api_key}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
        f"&state={nonce}"
    )
    from fastapi.responses import RedirectResponse
    return RedirectResponse(auth_url)


@app.get("/api/auth")
async def shopify_oauth_callback(request: Request):
    """Handle Shopify OAuth callback — exchange code for access token."""
    code = request.query_params.get("code", "")
    shop = request.query_params.get("shop", "")

    if not code or not shop:
        raise HTTPException(status_code=400, detail="Missing code or shop parameter")

    # Exchange code for permanent access token
    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://{shop}/admin/oauth/access_token",
            json={
                "client_id": settings.shopify_api_key,
                "client_secret": settings.shopify_api_secret,
                "code": code,
            },
        )

    if response.status_code != 200:
        logger.error("OAuth token exchange failed: %s", response.text)
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {response.text}")

    data = response.json()
    access_token = data.get("access_token", "")
    scope = data.get("scope", "")

    logger.info("OAuth complete for %s, scopes: %s", shop, scope)

    # Show the token (one-time setup page)
    return {
        "status": "success",
        "message": "Copy this access token and set it as SHOPIFY_ACCESS_TOKEN in Railway",
        "access_token": access_token,
        "scope": scope,
        "shop": shop,
    }


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


# ── Storefront Concierge ──


@app.post("/api/chat")
async def concierge_chat(request: Request):
    """AI concierge chat endpoint for the storefront.

    Accepts: {"message": "...", "history": [{"role": "user", "content": "..."}]}
    Returns: {"response": "...", "products": [...], "suggested_questions": [...]}
    """
    try:
        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message required")

        history = body.get("history", [])

        from src.agents.concierge import StorefrontConcierge
        concierge = StorefrontConcierge()
        result = await concierge.chat(message, conversation_history=history)
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("Concierge chat failed")
        return {
            "response": "I'm sorry, I'm having trouble right now. Please email us at hello@pinakajewellery.com and we'll help you personally.",
            "products": [],
            "suggested_questions": [],
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


@app.post("/webhook/shopify/refund")
async def shopify_refund_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle refunds/create webhooks."""
    return await handle_refund_webhook(request, background_tasks)


# ── ShipStation Webhook ──

@app.post("/webhook/shipstation")
async def shipstation_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle ShipStation tracking/shipment webhooks."""
    # Validate shared secret via query param
    secret = request.query_params.get("secret", "")
    if settings.shipstation_webhook_secret and secret != settings.shipstation_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret")

    data = await request.json()
    resource_url = data.get("resource_url", "")
    resource_type = data.get("resource_type", "")

    if not resource_url:
        return {"status": "ignored", "reason": "no_resource_url"}

    async def _process_tracking():
        shipping = ShippingProcessor()
        try:
            await shipping.handle_tracking_update(resource_url, resource_type)
        except Exception:
            logger.exception("ShipStation webhook processing failed: %s", resource_url)
        finally:
            await shipping.close()

    background_tasks.add_task(_process_tracking)
    return {"status": "received"}


# ── Inbound Email (SendGrid Inbound Parse) ──

@app.post("/inbound/email")
async def inbound_email(request: Request):
    """Receive customer emails via SendGrid Inbound Parse webhook."""
    return await handle_inbound_email(request)


# ── Chargeback Evidence ──

async def verify_dashboard_password(request: Request) -> None:
    """Verify dashboard password for founder-facing API endpoints."""
    auth = request.query_params.get("password", "")
    if not settings.dashboard_password or auth != settings.dashboard_password:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/api/orders/{order_id}/evidence")
async def get_order_evidence(order_id: int, request: Request):
    """Get chargeback evidence package for an order. Protected by dashboard_password."""
    await verify_dashboard_password(request)

    db = AsyncDatabase()
    evidence = await db.get_chargeback_evidence(order_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="Order not found")

    return {"status": "ok", "evidence": evidence}


# ── Product Management ──

@app.post("/api/products/upload", dependencies=[Depends(verify_cron_secret)])
async def upload_product(request: Request):
    """Upload a product JSON, save to Supabase, and embed for RAG search."""
    from src.product.embeddings import ProductEmbeddings

    data = await request.json()
    product = Product(**data)

    # Save to Supabase
    db = AsyncDatabase()
    await db.upsert_product({
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "materials": product.materials.model_dump() if hasattr(product.materials, 'model_dump') else product.materials,
        "pricing": {k: v.model_dump() if hasattr(v, 'model_dump') else v for k, v in product.pricing.items()},
        "story": product.story,
        "care_instructions": product.care_instructions,
        "occasions": product.occasions,
        "certification": product.certification.model_dump() if product.certification else None,
        "tags": product.tags,
    })

    # Embed immediately
    embeddings = ProductEmbeddings()
    embeddings.embed_product(product)

    return {
        "status": "ok",
        "sku": product.sku,
        "total_products": embeddings.product_count(),
    }


# ── Ad Creative Generation (Phase 6.1) ──
#
# Internal route for programmatic generation (future cron / CLI / other services).
# The dashboard uses its own router route at /dashboard/ad-creatives/generate.
# Gated via verify_cron_secret to prevent anonymous Claude/Meta burn.

@app.post("/api/ad-creatives/generate", dependencies=[Depends(verify_cron_secret)])
async def api_generate_ad_creatives(request: Request):
    """Generate N ad creative variants for a SKU. Returns the generation batch_id.

    Request body: {"sku": "DTB-LG-7-14KYG", "n_variants": 3}

    Unlike the dashboard route, this endpoint blocks until Claude responds (no
    BackgroundTasks). Used for cron/CLI where waiting is fine.
    """
    import uuid as _uuid

    from src.marketing.ad_generator import AdCreativeGenerator, AdGeneratorError

    data = await request.json()
    sku = data.get("sku", "")
    n_variants = int(data.get("n_variants", 3))

    if not sku:
        return {"status": "error", "error": "sku is required"}

    db = AsyncDatabase()
    product = await db.get_product_by_sku(sku)
    if not product:
        return {"status": "error", "error": f"Product {sku} not found"}

    try:
        gen = AdCreativeGenerator()
        variants, batch_id, dna_hash = gen.generate(product, n_variants=n_variants)
    except AdGeneratorError as e:
        return {"status": "error", "error": str(e)}

    rows = [
        v.to_db_row(sku=sku, generation_batch_id=batch_id, brand_dna_hash=dna_hash)
        for v in variants
    ]
    await db.create_ad_creative_batch(rows)
    return {
        "status": "ok",
        "batch_id": batch_id,
        "variant_count": len(variants),
        "sku": sku,
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

    # Persist draft to Supabase for Slack approval flow
    db = AsyncDatabase()
    draft = await db.create_listing_draft({
        "sku": product.sku,
        "title": result["title"],
        "description": result["description"],
        "tags": result.get("tags", []),
        "status": "pending_review",
    })

    # Send to Slack for founder review (use draft ID so approval handler can find it)
    slack = SlackNotifier()
    await slack.send_listing_review(
        title=result["title"],
        description=result["description"],
        tags=result["tags"],
        listing_draft_id=str(draft.get("id", product.sku)),
    )

    result["draft_id"] = draft.get("id")
    return result


# ── Cron Endpoints (secured) ──

@app.post("/cron/sync-products", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_products():
    """Load products from Supabase and embed for RAG search."""
    from src.product.embeddings import ProductEmbeddings
    from src.product.schema import Product

    db = AsyncDatabase()
    embeddings = ProductEmbeddings()
    before = embeddings.product_count()
    embedded = 0

    products = await db.get_all_products()
    for p in products:
        try:
            product_obj = Product(
                sku=p["sku"],
                name=p["name"],
                category=p.get("category", ""),
                materials=p.get("materials", {}),
                pricing=p.get("pricing", {}),
                story=p.get("story", ""),
                care_instructions=p.get("care_instructions", ""),
                occasions=p.get("occasions", []),
                certification=p.get("certification"),
                tags=p.get("tags", []),
            )
            embeddings.embed_product(product_obj)
            embedded += 1
        except Exception:
            logger.exception("Failed to embed product %s", p.get("sku"))

    return {
        "status": "ok",
        "module": "product_sync",
        "embedded": embedded,
        "total_in_index": embeddings.product_count(),
        "was": before,
    }


@app.post("/cron/sync-shopify-products", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_shopify_products():
    """Pull products from Shopify, embed them in ChromaDB, and backfill images to Supabase.

    Phase 6.1 addition: also syncs `products.images` from Shopify images[].src. Without
    this, `ad_creatives` generation would fail for any product whose images weren't manually
    set via the dashboard (which is every auto-synced product today). Matches Shopify
    products to Supabase rows via `shopify_product_id` (unique).
    """
    from src.product.embeddings import ProductEmbeddings

    shopify = ShopifyClient()
    embeddings = ProductEmbeddings()
    db = AsyncDatabase()
    embedded = 0
    images_synced = 0
    images_skipped = 0

    try:
        shopify_products = await shopify.get_products(limit=50)

        # Build a lookup of existing Supabase products by shopify_product_id for O(1) matching
        existing = await db.get_all_products()
        by_shopify_id = {
            int(p["shopify_product_id"]): p
            for p in existing
            if p.get("shopify_product_id")
        }

        for sp in shopify_products:
            embeddings.embed_shopify_product(sp)
            embedded += 1

            sp_id = sp.get("id")
            if not sp_id:
                continue

            supabase_row = by_shopify_id.get(int(sp_id))
            if not supabase_row:
                # Product is in Shopify but not in Supabase — skip (dashboard handles creation)
                images_skipped += 1
                continue

            # Extract image URLs from Shopify's images[] payload
            image_urls = [img.get("src", "") for img in sp.get("images", []) if img.get("src")]
            if not image_urls:
                continue

            # Only write if different (avoid unnecessary UPDATEs + trigger firings)
            current = supabase_row.get("images") or []
            if list(current) != image_urls:
                await db.update_product_images(supabase_row["sku"], image_urls)
                images_synced += 1
    finally:
        await shopify.close()

    return {
        "status": "ok",
        "module": "shopify_product_sync",
        "embedded": embedded,
        "images_synced": images_synced,
        "images_skipped_no_supabase_row": images_skipped,
        "total_in_index": embeddings.product_count(),
    }


@app.post("/cron/daily-stats", dependencies=[Depends(verify_cron_secret)])
async def cron_daily_stats():
    """Collect daily stats from Shopify orders and customers."""
    db = AsyncDatabase()
    yesterday = date.today() - timedelta(days=1)

    revenue = await db.get_total_revenue(yesterday, yesterday)

    orders = await db.get_orders_by_status("paid")
    yesterday_orders = [
        o for o in orders
        if o.get("created_at", "").startswith(yesterday.isoformat())
    ]

    new_customers = await db.get_customers_by_lifecycle("first_purchase")
    yesterday_new = [
        c for c in new_customers
        if c.get("created_at", "").startswith(yesterday.isoformat())
    ]

    repeat_customers = await db.get_repeat_customer_count()
    await db.upsert_daily_stats({
        "date": yesterday.isoformat(),
        "revenue": revenue,
        "order_count": len(yesterday_orders),
        "new_customers": len(yesterday_new),
        "repeat_customers": repeat_customers,
    })

    return {"status": "ok", "module": "stats", "orders_counted": len(yesterday_orders)}


@app.post("/cron/reconcile-orders", dependencies=[Depends(verify_cron_secret)])
async def cron_reconcile_orders():
    """Poll Shopify for orders missed by webhooks (every 30 min). Also checks webhook health."""
    from src.api.shopify_webhooks import _process_order

    shopify = ShopifyClient()
    db = AsyncDatabase()
    slack = SlackNotifier()
    reconciled = 0

    try:
        since = (datetime.utcnow() - timedelta(minutes=35)).isoformat()
        orders = await shopify.get_orders(created_at_min=since, limit=50)

        for order in orders:
            if await db.get_order_by_shopify_id(order["id"]):
                continue
            await _process_order(order)
            reconciled += 1

        # Webhook health check (every 30 min)
        await _check_webhook_health(shopify, slack)
    finally:
        await shopify.close()

    return {"status": "ok", "module": "reconcile", "reconciled": reconciled}


@app.post("/cron/check-deliveries", dependencies=[Depends(verify_cron_secret)])
async def cron_check_deliveries():
    """Poll for shipped orders that may have been delivered. Collects chargeback evidence."""
    db = AsyncDatabase()
    shipping = ShippingProcessor()
    checked = 0
    delivered = 0

    try:
        # Get orders shipped 3+ days ago that haven't been marked delivered
        shipped_orders = await db.get_shipped_orders_pending_delivery(shipped_before_days=3)

        for order in shipped_orders:
            shopify_order_id = order.get("shopify_order_id")
            shipstation_order_id = order.get("shipstation_order_id")
            if not shipstation_order_id:
                continue

            checked += 1
            try:
                tracking_info = await shipping.get_tracking(shipstation_order_id)
                delivery_date = tracking_info.get("delivery_date")

                if delivery_date:
                    # Mark as delivered
                    await db.update_order_status(
                        shopify_order_id, "delivered",
                        delivered_at=delivery_date,
                    )

                    # Send delivery confirmation email
                    buyer_email = order.get("buyer_email", "")
                    if buyer_email:
                        from src.core.email import EmailSender
                        email = EmailSender()
                        email.send_delivery_confirmation(
                            to_email=buyer_email,
                            customer_name=order.get("buyer_name", "") or buyer_email,
                            order_number=str(shopify_order_id),
                        )

                    # Collect chargeback evidence
                    await shipping.collect_evidence_on_delivery(shopify_order_id)
                    delivered += 1

            except Exception:
                logger.exception("Delivery check failed for order #%s", shopify_order_id)
    finally:
        await shipping.close()

    return {"status": "ok", "module": "delivery_check", "checked": checked, "delivered": delivered}


@app.post("/cron/crafting-updates", dependencies=[Depends(verify_cron_secret)])
async def cron_crafting_updates():
    """Check for orders needing crafting update emails (Day 2-3 post-order)."""
    db = AsyncDatabase()
    slack = SlackNotifier()
    classifier = MessageClassifier()
    sent = 0

    orders = await db.get_orders_needing_crafting_update(settings.crafting_update_delay_days)

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
    db = AsyncDatabase()
    slack = SlackNotifier()
    classifier = MessageClassifier()
    flagged = 0

    # Transition stale "created" carts to "abandoned" (60-min delay)
    newly_abandoned = await db.mark_abandoned_carts(settings.abandoned_cart_delay_minutes)
    if newly_abandoned:
        logger.info("Marked %d carts as abandoned", newly_abandoned)

    carts = await db.get_abandoned_carts_pending_recovery()

    for cart in carts:
        customer_email = cart.get("customer_email", "")
        cart_value = float(cart.get("cart_value", 0))
        if cart_value < 1:
            continue

        # Look up customer context
        customer_context = ""
        customer_name = customer_email or "Anonymous"
        if customer_email:
            customer = await db.get_customer_by_email(customer_email)
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
        await db.upsert_cart_event({
            "shopify_checkout_token": cart["shopify_checkout_token"],
            "recovery_email_status": "pending",
        })

        # Fire observation for agent awareness
        try:
            from src.agents.observations import observe_cart_abandoned
            await observe_cart_abandoned(
                email=customer_email or "anonymous",
                cart_value=cart_value,
                items=product_names,
            )
        except Exception:
            pass  # Non-critical

        flagged += 1

    return {"status": "ok", "module": "cart_recovery", "flagged": flagged}


REQUIRED_WEBHOOK_TOPICS = {
    "orders/create": "/webhook/shopify/orders",
    "customers/create": "/webhook/shopify/customers",
    "checkouts/create": "/webhook/shopify/checkouts",
    "refunds/create": "/webhook/shopify/refund",
}


async def _check_webhook_health(shopify: ShopifyClient, slack: SlackNotifier) -> list[str]:
    """Verify all Shopify webhook subscriptions are active. Auto-re-register missing ones."""
    base_url = settings.webhook_base_url
    if not base_url:
        logger.warning("WEBHOOK_BASE_URL not set, skipping webhook health check")
        return []

    missing = []
    re_registered = []
    failed = []

    try:
        existing = await shopify.get_webhooks()
        active_topics = {wh["topic"] for wh in existing}
        missing = sorted(set(REQUIRED_WEBHOOK_TOPICS.keys()) - active_topics)
    except Exception as e:
        logger.error("Webhook health check failed: %s", e)
        await slack.send_alert(f"Webhook health check failed: {e}", level="warning")
        return list(REQUIRED_WEBHOOK_TOPICS.keys())

    if not missing:
        return []

    logger.warning("Missing webhook subscriptions: %s", missing)

    # Auto-re-register missing webhooks
    for topic in missing:
        path = REQUIRED_WEBHOOK_TOPICS[topic]
        address = f"{base_url}{path}"
        try:
            await shopify.create_webhook(topic, address)
            re_registered.append(topic)
            logger.info("Re-registered webhook: %s -> %s", topic, address)
        except Exception as e:
            failed.append(topic)
            logger.error("Failed to re-register webhook %s: %s", topic, e)

    # Alert on re-registrations
    if re_registered:
        await slack.send_webhook_health_alert(
            re_registered=re_registered,
            failed=failed,
        )

    # Urgent alert if any failed to re-register
    if failed:
        await slack.send_alert(
            f":rotating_light: URGENT: Failed to re-register webhooks: {', '.join(failed)}. "
            "Manual intervention required.",
            level="error",
        )

    return failed  # Return only topics that couldn't be fixed


@app.post("/cron/morning-digest", dependencies=[Depends(verify_cron_secret)])
async def cron_morning_digest():
    """Daily morning digest sent to Slack at 8 AM."""
    db = AsyncDatabase()
    slack = SlackNotifier()

    yesterday = date.today() - timedelta(days=1)
    stats = await db.get_stats_range(yesterday, yesterday)
    pending = await db.get_pending_messages()

    revenue = float(stats[0].get("revenue", 0)) if stats else 0.0
    orders = int(stats[0].get("order_count", 0)) if stats else 0
    new_customers = int(stats[0].get("new_customers", 0)) if stats else 0

    abandoned = await db.get_abandoned_carts_pending_recovery()

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
                    f":rotating_light: *Webhook re-registration FAILED for:* {', '.join(missing_webhooks)}\n"
                    "Auto-recovery attempted but failed. Manual intervention required."
                ),
            },
        })

    await slack.send_blocks(blocks, text=f"Morning digest: ${revenue:,.2f} revenue, {orders} orders")
    return {"status": "ok", "module": "digest"}


@app.post("/cron/weekly-rollup", dependencies=[Depends(verify_cron_secret)])
async def cron_weekly_rollup():
    """Weekly rollup sent to Slack on Mondays at 9 AM."""
    db = AsyncDatabase()
    slack = SlackNotifier()

    end = date.today()
    start = end - timedelta(days=7)
    stats = await db.get_stats_range(start, end)

    total_revenue = sum(float(s.get("revenue", 0)) for s in stats)
    total_orders = sum(int(s.get("order_count", 0)) for s in stats)
    total_new_customers = sum(int(s.get("new_customers", 0)) for s in stats)

    customer_count = await db.get_customer_count()
    repeat_count = await db.get_repeat_customer_count()

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


@app.post("/cron/weekly-roas", dependencies=[Depends(verify_cron_secret)])
async def cron_weekly_roas():
    """Weekly ROAS report with budget recommendation."""
    from src.marketing.ads import AdsTracker

    tracker = AdsTracker()
    errors = []
    try:
        result = await tracker.run_weekly_roas_report()
    except Exception as e:
        logger.exception("Weekly ROAS report failed")
        errors.append(str(e))
        return {"status": "partial_failure", "module": "ads", "errors": errors}

    return {
        "status": "ok",
        "module": "ads",
        "roas": result.roas,
        "recommendation": result.recommendation,
        "total_spend": result.total_ad_spend,
        "total_revenue": result.total_revenue,
    }


@app.post("/cron/heartbeat", dependencies=[Depends(verify_cron_secret)])
async def cron_heartbeat():
    """Agent awareness heartbeat — runs every 30 min.

    Cheap SQL checks first (stuck orders, unanswered messages, shipping delays).
    Only invokes Claude when issues are found. Posts alerts to Slack.
    """
    from src.agents.heartbeat import Heartbeat

    hb = Heartbeat()
    try:
        result = await hb.beat()
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("Heartbeat failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/marketing-snapshot", dependencies=[Depends(verify_cron_secret)])
async def cron_marketing_snapshot():
    """Ad performance data collection — runs every 6 hours.

    Pulls current spend, impressions, CTR, CPC from Meta (and Google when ready).
    Writes an observation but takes NO actions. Zero LLM cost.
    Only alerts Slack if an anomaly is detected (spend > 2x budget, tracking broken).
    """
    from zoneinfo import ZoneInfo
    from src.core.database import AsyncDatabase
    from src.marketing.meta_ads import MetaAdsClient, MetaAdsError
    from src.agents.observations import observe, observe_roas_change

    tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(tz).date()
    db = AsyncDatabase()
    errors = []

    # Pull today's spend so far
    meta_spend = 0.0
    meta = MetaAdsClient()
    if meta.is_configured:
        try:
            result = await meta.get_daily_spend(today)
            meta_spend = result.spend
        except MetaAdsError as e:
            errors.append(f"meta: {e}")

    # Get today's revenue from orders
    today_stats = await db.get_stats_range(today, today)
    revenue = sum(float(s.get("revenue", 0)) for s in today_stats) if today_stats else 0

    # Calculate intraday ROAS
    total_spend = meta_spend
    roas = revenue / total_spend if total_spend > 0 else 0

    # Write observation (always — this is the data collection)
    await observe(
        source="cron:marketing_snapshot",
        category="marketing",
        severity="info",
        summary=f"6h snapshot: spend ${total_spend:,.2f}, revenue ${revenue:,.2f}, ROAS {roas:.1f}x",
        entity_type="metric",
        entity_id="daily_snapshot",
        data={"spend": total_spend, "revenue": revenue, "roas": roas, "meta_spend": meta_spend},
    )

    # Anomaly detection (no LLM — just threshold checks)
    alerts = []
    if total_spend > settings.max_daily_ad_budget * 1.5:
        alerts.append(f"Spend anomaly: ${total_spend:,.2f} exceeds 150% of daily cap (${settings.max_daily_ad_budget})")
        await observe(
            source="cron:marketing_snapshot",
            category="marketing",
            severity="critical",
            summary=f"SPEND ALERT: ${total_spend:,.2f} exceeds 150% of ${settings.max_daily_ad_budget} daily cap",
            entity_type="metric",
            entity_id="spend_anomaly",
        )

    if alerts:
        slack = SlackNotifier()
        for alert in alerts:
            await slack.send_blocks([
                {"type": "header", "text": {"type": "plain_text", "text": ":rotating_light: Marketing Anomaly"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": alert}},
            ], text=alert)

    return {
        "status": "ok",
        "spend": total_spend,
        "revenue": revenue,
        "roas": round(roas, 2),
        "anomalies": len(alerts),
        "errors": errors,
    }


@app.post("/cron/marketing-weekly", dependencies=[Depends(verify_cron_secret)])
async def cron_marketing_weekly():
    """Weekly marketing strategy review — runs Monday 9 AM ET.

    The Marketing Agent analyzes the full week's data: ROAS trends, budget
    efficiency, creative fatigue signals, seasonal windows, product margins.
    Posts a structured strategy report to Slack with recommendations.
    Uses Claude (the only marketing cron that does).
    """
    from src.agents.marketing import MarketingAgent
    from src.agents.context import ContextAssembler

    try:
        agent = MarketingAgent()
        context = await ContextAssembler().for_marketing()

        result = await agent.run(
            "Run the weekly marketing strategy review. Analyze this week's ad performance, "
            "check for seasonal windows, review product margins, and post a full strategy "
            "report to Slack with budget recommendations.",
            context,
        )

        return {
            "status": "ok",
            "success": result.success,
            "escalated": result.escalated,
            "confidence": result.confidence,
            "tokens_used": result.tokens_used,
            "actions": len(result.actions_taken),
        }
    except Exception as e:
        logger.exception("Weekly marketing review failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/sync-ad-spend", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_ad_spend():
    """Pull yesterday's ad spend from Meta (and Google when configured).

    Updates daily_stats with ad_spend_meta. Also syncs legacy ad_spend column
    (ad_spend = ad_spend_google + ad_spend_meta) so finance reads stay correct.
    Scheduled: daily 6 AM ET, before morning digest.
    """
    from zoneinfo import ZoneInfo

    from src.core.database import AsyncDatabase
    from src.marketing.google_ads import GoogleAdsClient, GoogleAdsError
    from src.marketing.meta_ads import MetaAdsClient, MetaAdsError

    tz = ZoneInfo(settings.business_timezone)
    yesterday = (datetime.now(tz) - timedelta(days=1)).date()
    errors = []
    meta_spend = None

    # ── Meta ad spend ──
    meta = MetaAdsClient()
    if meta.is_configured:
        try:
            result = await meta.get_daily_spend(yesterday)
            meta_spend = result.spend
        except MetaAdsError as e:
            logger.error("Meta ad spend sync failed for %s: %s", yesterday, e)
            errors.append(f"meta: {e}")
            try:
                slack = SlackNotifier()
                await slack.send_alert(
                    f":warning: Meta ad spend sync failed for {yesterday}: {e}",
                )
            except Exception:
                logger.exception("Slack alert for Meta spend failure also failed")
    else:
        logger.info("Meta Ads not configured, skipping Meta spend sync")

    # ── Google ad spend ──
    google_spend = None
    google = GoogleAdsClient()
    if google.is_configured:
        try:
            result = await google.get_daily_spend(yesterday)
            google_spend = result.spend
        except GoogleAdsError as e:
            logger.error("Google ad spend sync failed for %s: %s", yesterday, e)
            errors.append(f"google: {e}")
            try:
                slack = SlackNotifier()
                await slack.send_alert(
                    f":warning: Google ad spend sync failed for {yesterday}: {e}",
                )
            except Exception:
                logger.exception("Slack alert for Google spend failure also failed")
    else:
        logger.info("Google Ads not configured, skipping Google spend sync")

    # ── Upsert daily_stats ──
    if meta_spend is not None or google_spend is not None:
        db = AsyncDatabase()
        # Fetch existing row to merge (don't overwrite non-spend fields)
        existing = await db.get_stats_range(yesterday, yesterday)
        existing_row = existing[0] if existing else {}

        current_meta = float(existing_row.get("ad_spend_meta", 0))
        current_google = float(existing_row.get("ad_spend_google", 0))

        new_meta = meta_spend if meta_spend is not None else current_meta
        new_google = google_spend if google_spend is not None else current_google

        await db.upsert_daily_stats({
            "date": yesterday.isoformat(),
            "ad_spend_meta": new_meta,
            "ad_spend_google": new_google,
            "ad_spend": new_meta + new_google,  # Sync legacy column (Plan Fix 3)
            "ad_spend_synced_at": datetime.now(tz).isoformat(),
            "ad_spend_source": "api",
        })

        logger.info(
            "Ad spend synced for %s: meta=$%.2f, google=$%.2f, total=$%.2f",
            yesterday, new_meta, new_google, new_meta + new_google,
        )

    if errors:
        return {
            "status": "partial_failure",
            "module": "ad_spend",
            "date": yesterday.isoformat(),
            "errors": errors,
            "meta_spend": meta_spend,
            "google_spend": google_spend,
        }

    return {
        "status": "ok",
        "module": "ad_spend",
        "date": yesterday.isoformat(),
        "meta_spend": meta_spend,
        "google_spend": google_spend,
    }


@app.post("/cron/sync-meta-catalog", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_meta_catalog():
    """Sync active products to Meta Commerce Manager catalog for Dynamic Product Ads.

    Fetches all Shopify-published products from Supabase, maps them to Meta
    catalog format, and pushes via Catalog Batch API.
    Scheduled: daily 5 AM ET (before ad spend sync at 6 AM).
    """
    from src.core.database import AsyncDatabase
    from src.marketing.meta_catalog import MetaCatalogSync

    errors = []
    meta_sync = MetaCatalogSync()

    if not meta_sync.is_configured:
        return {
            "status": "skipped",
            "module": "meta_catalog",
            "reason": "Meta Catalog not configured (missing token or catalog_id)",
        }

    db = AsyncDatabase()
    try:
        products = await db.get_all_active_products()
    except Exception as e:
        logger.exception("Failed to fetch products for catalog sync")
        return {
            "status": "partial_failure",
            "module": "meta_catalog",
            "errors": [f"DB fetch failed: {e}"],
        }

    try:
        result = await meta_sync.sync_products(products)
    except Exception as e:
        logger.exception("Meta catalog sync failed")
        errors.append(str(e))
        try:
            slack = SlackNotifier()
            await slack.send_alert(
                f":warning: Meta catalog sync failed: {e}",
            )
        except Exception:
            logger.exception("Slack alert for catalog sync failure also failed")
        return {
            "status": "partial_failure",
            "module": "meta_catalog",
            "errors": errors,
        }

    logger.info(
        "Meta catalog sync: %d products, %d synced, %d failed",
        len(products), result.items_synced, result.items_failed,
    )

    return {
        "status": result.status,
        "module": "meta_catalog",
        "total_products": len(products),
        "items_synced": result.items_synced,
        "items_failed": result.items_failed,
        "errors": result.errors,
    }


@app.post("/cron/sync-google-merchant", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_google_merchant():
    """Sync active products to Google Merchant Center for Shopping ads.

    Scheduled: daily 5:15 AM ET (staggered 15 min after Meta catalog).
    """
    from src.core.database import AsyncDatabase
    from src.marketing.google_merchant import GoogleMerchantSync

    errors = []
    merchant = GoogleMerchantSync()

    if not merchant.is_configured:
        return {
            "status": "skipped",
            "module": "google_merchant",
            "reason": "Google Merchant not configured",
        }

    db = AsyncDatabase()
    try:
        products = await db.get_all_active_products()
    except Exception as e:
        logger.exception("Failed to fetch products for Merchant sync")
        return {
            "status": "partial_failure",
            "module": "google_merchant",
            "errors": [f"DB fetch failed: {e}"],
        }

    try:
        result = await merchant.sync_products(products)
    except Exception as e:
        logger.exception("Google Merchant sync failed")
        errors.append(str(e))
        try:
            slack = SlackNotifier()
            await slack.send_alert(f":warning: Google Merchant sync failed: {e}")
        except Exception:
            logger.exception("Slack alert for Merchant sync failure also failed")
        return {
            "status": "partial_failure",
            "module": "google_merchant",
            "errors": errors,
        }

    return {
        "status": result.status,
        "module": "google_merchant",
        "total_products": len(products),
        "items_synced": result.items_synced,
        "items_failed": result.items_failed,
        "errors": result.errors,
    }


@app.post("/cron/reorder-reminders", dependencies=[Depends(verify_cron_secret)])
async def cron_reorder_reminders():
    """Find reorder candidates and post AI-drafted reminders to Slack for review."""
    from src.customer.reorder import ReorderEngine

    engine = ReorderEngine()
    slack = SlackNotifier()

    candidates = await engine.find_reorder_candidates()
    if not candidates:
        return {"status": "ok", "module": "reorder", "candidates": 0}

    drafted = 0
    for candidate in candidates:
        customer_name = candidate.get("name", "Customer")
        customer_email = candidate.get("email", "")
        customer_id = candidate.get("customer_id")
        last_items = candidate.get("last_order_items", "jewelry purchase")
        trigger_days = candidate.get("trigger_days", 90)

        try:
            draft = await engine.draft_reminder(
                customer_name=customer_name,
                last_order_items=last_items,
                trigger_days=trigger_days,
            )
            await slack.send_reorder_reminder_review(
                customer_name=customer_name,
                customer_email=customer_email,
                last_order_number=candidate.get("last_order_number", ""),
                last_order_total=float(candidate.get("last_order_total", 0)),
                days_since=trigger_days,
                email_draft=draft,
                customer_id=customer_id,
            )
            drafted += 1
        except Exception:
            logger.exception("Failed to draft reorder reminder for %s", customer_email)

    return {"status": "ok", "module": "reorder", "candidates": len(candidates), "drafted": drafted}


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
    db = AsyncDatabase()
    email = EmailSender()

    # ── Customer Response Actions ──
    if action_id == "approve_response":
        msg = await db.get_pending_messages()
        target = next((m for m in msg if str(m.get("id")) == value), None)
        if target and target.get("ai_draft"):
            email.send_service_reply(
                to_email=target.get("customer_email", ""),
                customer_name=target.get("buyer_name", ""),
                subject=target.get("subject", "Re: Your inquiry"),
                email_body=target["ai_draft"],
            )
            await db.update_message_status(target["id"], "sent", human_approved=True)

        tombstone = SlackNotifier.tombstone_blocks("Approved & Sent", f"Message #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "reject_response":
        await db.update_message_status(int(value), "rejected")
        tombstone = SlackNotifier.tombstone_blocks("Rejected", f"Message #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Abandoned Cart Recovery Actions ──
    elif action_id == "approve_cart_recovery":
        cart = await db.get_cart_by_id(int(value))
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
            await db.upsert_cart_event({
                "shopify_checkout_token": cart["shopify_checkout_token"],
                "recovery_email_status": "sent",
            })
        tombstone = SlackNotifier.tombstone_blocks("Recovery Email Sent", f"Cart #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_cart_recovery":
        cart = await db.get_cart_by_id(int(value))
        if cart:
            await db.upsert_cart_event({
                "shopify_checkout_token": cart["shopify_checkout_token"],
                "recovery_email_status": "cancelled",
            })
        tombstone = SlackNotifier.tombstone_blocks("Recovery Skipped", f"Cart #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Crafting Update Actions ──
    elif action_id == "approve_crafting_update":
        order = await db.get_order_by_shopify_id(int(value))
        if order:
            customer_id = order.get("customer_id")
            customer = (await db.get_customer_by_shopify_id(customer_id)) if customer_id else None
            customer_email = order.get("buyer_email", "")
            customer_name = order.get("buyer_name", "")

            if customer_email:
                email.send_crafting_update(
                    to_email=customer_email,
                    customer_name=customer_name,
                    order_number=str(value),
                    email_body="",  # Template handles the content
                )
        await db.update_order_status(int(value), "crafting_update_sent")
        tombstone = SlackNotifier.tombstone_blocks("Crafting Update Sent", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_crafting_update":
        tombstone = SlackNotifier.tombstone_blocks("Update Skipped", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Shipping/Fraud Actions ──
    elif action_id == "approve_shipment":
        await db.update_order_status(int(value), "approved_for_shipping")
        tombstone = SlackNotifier.tombstone_blocks("Shipment Approved", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "hold_order":
        await db.update_order_status(int(value), "held_for_review")
        tombstone = SlackNotifier.tombstone_blocks("Order Held", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "cancel_order":
        await db.update_order_status(int(value), "cancelled")
        tombstone = SlackNotifier.tombstone_blocks("Order Cancelled", f"Order #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Listing Actions ──
    elif action_id == "approve_listing":
        draft = await db.get_listing_draft(int(value))
        if draft:
            shopify = ShopifyClient()
            try:
                product = await shopify.create_product(
                    title=draft["title"],
                    body_html=draft["description"],
                    tags=draft.get("tags", []),
                )
                shopify_product_id = product.get("id")
                await db.update_listing_draft_status(
                    draft["id"], "published", shopify_product_id=shopify_product_id,
                )
                detail = f"Listing #{value} published to Shopify as draft (ID: {shopify_product_id})"
            except Exception as e:
                logger.exception("Failed to publish listing #%s to Shopify", value)
                detail = f"Listing #{value} — Shopify publish failed: {e}"
            finally:
                await shopify.close()
        else:
            detail = f"Listing #{value} not found"
        tombstone = SlackNotifier.tombstone_blocks("Published to Shopify (Draft)", detail, timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "reject_listing":
        await db.update_listing_draft_status(int(value), "rejected")
        tombstone = SlackNotifier.tombstone_blocks("Listing Rejected", f"Draft #{value}", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Budget Actions ──
    elif action_id == "apply_budget_change":
        logger.info("Budget change acknowledged: $%s/day", value)
        tombstone = SlackNotifier.tombstone_blocks(
            "Budget Change Noted", f"${value}/day — apply manually in ad platforms", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "dismiss_budget":
        tombstone = SlackNotifier.tombstone_blocks("Dismissed", "Budget recommendation", timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    # ── Reorder Reminder Actions ──
    elif action_id == "approve_reorder":
        reorder_data = json.loads(value)
        customer_email = reorder_data.get("customer_email", "")
        customer_name = reorder_data.get("customer_name", "Customer")
        customer_id = reorder_data.get("customer_id")
        # The draft text is in the message blocks — extract from Slack payload
        draft_text = ""
        for block in payload.get("message", {}).get("blocks", []):
            if block.get("block_id") == "reorder_draft":
                draft_text = block.get("text", {}).get("text", "")
                break

        if customer_email and draft_text:
            email.send_reorder_reminder(
                to_email=customer_email,
                customer_name=customer_name,
                email_body=draft_text,
            )
            if customer_id:
                await db.update_customer_reorder_sent(int(customer_id))

        tombstone = SlackNotifier.tombstone_blocks(
            "Reorder Reminder Sent", f"{customer_name} ({customer_email})", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_reorder":
        tombstone = SlackNotifier.tombstone_blocks(
            "Reorder Skipped", f"Customer #{value}", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    # ── Dismiss/Edit Actions ──
    elif action_id in ("dismiss", "contact_customer_exception", "edit_response", "edit_cart_recovery", "edit_crafting_update", "edit_listing"):
        tombstone = SlackNotifier.tombstone_blocks("Dismissed", action_id, timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    else:
        logger.warning("Unknown Slack action: %s", action_id)

    return {"status": "ok"}
