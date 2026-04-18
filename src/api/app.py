"""FastAPI application — routes for health, cron endpoints, webhooks, and Slack interactivity."""

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import sentry_sdk
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request

from src.api.inbound_email import handle_inbound_email
from src.dashboard.web import router as dashboard_router
from src.api.shopify_webhooks import (
    handle_checkout_webhook,
    handle_customer_webhook,
    handle_order_webhook,
    handle_product_delete_webhook,
    handle_product_webhook,
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

# CORS for storefront chat widget (pinakajewellery.com) + Shopify Checkout UI Extensions
# (thank-you page survey) which run in a sandboxed iframe with shop.app / shopifycs.com origin.
from starlette.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pinakajewellery.com",
        "https://www.pinakajewellery.com",
        "https://pinaka-jewellery.myshopify.com",
        "https://shop.app",
    ],
    allow_origin_regex=r"https://([a-zA-Z0-9-]+\.)*shopifycs\.com|https://([a-zA-Z0-9-]+\.)*shopifycdn\.com|https://([a-zA-Z0-9-]+\.)*shopify\.com",
    allow_methods=["POST", "OPTIONS"],
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

    # Redirect to Shopify OAuth authorize.
    # Keep this string aligned with shopify.app.toml::access_scopes.
    scopes = "read_checkouts,read_customers,read_orders,read_products,read_shipping,write_customers,write_orders,write_products,write_shipping,write_content"
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


# ── Virtual Try-On ──


def _generate_wrist_mask(width: int, height: int) -> str:
    """Generate a black mask with narrow white band at wrist position.

    Band is 12% of image height, positioned at 42-54% (wrists sit
    slightly above center in typical portrait wrist photos).
    """
    from PIL import Image, ImageDraw

    mask = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(mask)
    band_top = int(height * 0.42)
    band_bottom = int(height * 0.54)
    draw.rectangle([(0, band_top), (width, band_bottom)], fill=(255, 255, 255))
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _build_tryon_prompt(product_title: str) -> str:
    """Build a bracelet-specific try-on prompt from the product title.

    Detects metal color, setting style, diamond color, and line type
    from the title to generate an accurate bracelet description.
    """
    t = product_title.lower()

    # Metal
    if "yellow gold" in t:
        metal = "warm yellow gold"
    elif "rose gold" in t:
        metal = "rose gold"
    else:
        metal = "polished white gold"

    # Setting style
    if "bezel" in t:
        setting = "round bezel cup settings"
    elif "u-prong" in t:
        setting = "U-shaped prong settings"
    elif "classic" in t:
        setting = "four-prong settings"
    else:
        setting = "prong settings"

    # Diamond color
    if "blue diamond" in t:
        stones = "alternating white and blue colored diamonds"
    elif "green diamond" in t:
        stones = "alternating white and green colored diamonds"
    elif "pink diamond" in t:
        stones = "alternating white and pink colored diamonds"
    else:
        stones = "uniform white round brilliant cut diamonds"

    # Line type
    line = "two parallel rows" if "double" in t else "a single continuous row"

    return (
        f"Replace the masked area with a diamond tennis bracelet circling the wrist. "
        f"The bracelet has {line} of {stones} individually set in {metal} "
        f"{setting}, total width 3mm. It forms a complete loop around the wrist "
        f"at the narrowest point, sitting flat against skin. Single catchlight per "
        f"diamond facet. Soft contact shadow where metal meets skin. "
        f"Preserve everything outside the mask exactly."
    )


@app.post("/api/try-on")
async def virtual_try_on(request: Request):
    """Virtual try-on: composite a bracelet onto a customer's wrist photo.

    Accepts: {"wrist_image": "base64...", "product_handle": "diamond-tennis-bracelet-lab-grown"}
    Returns: {"status": "success", "result_url": "...", "product_title": "..."}
    """
    try:
        body = await request.json()
        wrist_b64 = body.get("wrist_image", "").strip()
        product_handle = body.get("product_handle", "").strip()

        if not wrist_b64:
            raise HTTPException(status_code=400, detail="wrist_image required")
        if not product_handle:
            raise HTTPException(status_code=400, detail="product_handle required")

        # Strip data URI prefix if present
        if "," in wrist_b64 and wrist_b64.startswith("data:"):
            wrist_b64 = wrist_b64.split(",", 1)[1]

        # Validate size (base64 ~1.33x raw, so 13.3M chars ≈ 10MB)
        if len(wrist_b64) > 13_300_000:
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")

        # Look up product from Shopify
        shop = settings.shopify_shop_domain or "pinaka-jewellery.myshopify.com"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://{shop}/admin/api/2025-01/products.json",
                params={"handle": product_handle, "limit": 1, "fields": "title,images"},
                headers={"X-Shopify-Access-Token": settings.shopify_access_token},
            )
        products = resp.json().get("products", [])
        if not products:
            return {"status": "error", "message": "Product not found"}

        product_title = products[0].get("title", "Diamond Tennis Bracelet")
        product_image = ""
        images = products[0].get("images", [])
        if images:
            product_image = images[0].get("src", "")

        # Decode image to get dimensions for mask
        from PIL import Image

        raw_bytes = base64.b64decode(wrist_b64)
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size

        # Generate mask (white band at center where wrist likely is)
        mask_b64 = _generate_wrist_mask(w, h)

        # Build prompt with product-specific bracelet description
        prompt = _build_tryon_prompt(product_title)

        # Call Freepik Ideogram Image Edit
        if not settings.freepik_api_key:
            logger.error("FREEPIK_API_KEY not configured")
            return {"status": "error", "message": "Try-on service not configured"}

        freepik_headers = {
            "x-freepik-api-key": settings.freepik_api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.freepik.com/v1/ai/ideogram-image-edit",
                headers=freepik_headers,
                json={
                    "image": wrist_b64,
                    "mask": mask_b64,
                    "prompt": prompt,
                    "rendering_speed": "DEFAULT",
                    "style_type": "REALISTIC",
                    "magic_prompt": "ON",
                },
            )

        if resp.status_code != 200:
            logger.error("Freepik submit failed: %s %s", resp.status_code, resp.text[:300])
            return {"status": "error", "message": "Failed to start try-on generation"}

        task_id = resp.json().get("data", {}).get("task_id")
        if not task_id:
            return {"status": "error", "message": "No task ID returned"}

        # Poll for completion (max 60 seconds)
        result_url = None
        for _ in range(12):
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=10) as client:
                poll = await client.get(
                    f"https://api.freepik.com/v1/ai/ideogram-image-edit/{task_id}",
                    headers={"x-freepik-api-key": settings.freepik_api_key},
                )
            data = poll.json().get("data", {})
            status = data.get("status", "")
            if status == "COMPLETED" and data.get("generated"):
                result_url = data["generated"][0]
                break
            if status == "FAILED":
                logger.error("Freepik try-on failed for task %s", task_id)
                return {"status": "error", "message": "Generation failed. Try a different photo."}

        if not result_url:
            return {"status": "error", "message": "Generation timed out. Please try again."}

        return {
            "status": "success",
            "result_url": result_url,
            "product_title": product_title,
            "product_image": product_image,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Virtual try-on failed")
        return {"status": "error", "message": "Something went wrong. Please try again."}


# ── Pixel Event Relay (CAPI backstop for client-side events) ──

ALLOWED_PIXEL_EVENTS = {"ViewContent", "AddToCart", "InitiateCheckout"}


@app.post("/api/pixel/event")
async def pixel_event_relay(request: Request):
    """Relay a conversion event from the storefront to Meta CAPI server-side.

    Paired with the browser pixel (same event_id) for deduplication. Use when
    Shopify's native pixel is blocked or when we need signal Shopify doesn't
    capture (e.g. concierge-driven product views). Does NOT replace Shopify's
    native Meta channel — this is redundancy + custom events.

    Accepts JSON: {event_name, event_id, product_id?, value?, content_ids?,
    currency?, customer_email?, fbp?, fbc?, source_url?}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_name = str(body.get("event_name", "")).strip()
    event_id = str(body.get("event_id", "")).strip()
    if event_name not in ALLOWED_PIXEL_EVENTS:
        raise HTTPException(status_code=400, detail=f"event_name must be one of {sorted(ALLOWED_PIXEL_EVENTS)}")
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id required for browser/server dedup")

    try:
        value = float(body.get("value", 0))
    except (TypeError, ValueError):
        value = 0.0

    from src.marketing.meta_capi import MetaConversionsAPI
    capi = MetaConversionsAPI()
    if not capi.is_configured:
        return {"status": "skipped", "reason": "CAPI not configured"}

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")[:500]
    customer_email = (body.get("customer_email") or "").strip().lower()
    fbp = (body.get("fbp") or "").strip()
    fbc = (body.get("fbc") or "").strip()
    source_url = (body.get("source_url") or "").strip()[:500]

    sent = False
    if event_name == "ViewContent":
        sent = await capi.send_view_content(
            product_id=str(body.get("product_id", "")),
            value=value, event_id=event_id,
            currency=body.get("currency", "USD"),
            customer_email=customer_email,
            client_ip=client_ip, user_agent=user_agent,
            fbp=fbp, fbc=fbc, source_url=source_url,
        )
    elif event_name == "AddToCart":
        sent = await capi.send_add_to_cart(
            product_id=str(body.get("product_id", "")),
            value=value, event_id=event_id,
            quantity=int(body.get("quantity", 1) or 1),
            currency=body.get("currency", "USD"),
            customer_email=customer_email,
            client_ip=client_ip, user_agent=user_agent,
            fbp=fbp, fbc=fbc, source_url=source_url,
        )
    elif event_name == "InitiateCheckout":
        raw_ids = body.get("content_ids") or []
        content_ids = [str(c) for c in raw_ids if c] if isinstance(raw_ids, list) else []
        sent = await capi.send_initiate_checkout(
            content_ids=content_ids,
            value=value, event_id=event_id,
            currency=body.get("currency", "USD"),
            customer_email=customer_email,
            client_ip=client_ip, user_agent=user_agent,
            fbp=fbp, fbc=fbc, source_url=source_url,
        )

    return {"status": "sent" if sent else "failed", "event_name": event_name, "event_id": event_id}


# ── Post-Purchase Attribution Survey ──

ALLOWED_ATTRIBUTION_CHANNELS = {
    "instagram", "tiktok", "pinterest", "google_search",
    "meta_ads", "podcast", "friend", "press", "other",
}

ALLOWED_PURCHASE_REASONS = {
    "gift", "self_purchase", "anniversary", "milestone", "engagement", "other",
}


@app.post("/api/attribution/submit")
async def submit_attribution(request: Request):
    """Collect 'how did you hear about us' from the Shopify thank-you page.

    Accepts JSON: {shopify_order_id, customer_email?, channel_primary, channel_detail?,
    purchase_reason?, purchase_reason_detail?}

    Returns 200 on success or if already recorded (idempotent on refresh).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    shopify_order_id = str(body.get("shopify_order_id", "")).strip()
    channel_primary = str(body.get("channel_primary", "")).strip().lower()

    if not shopify_order_id:
        raise HTTPException(status_code=400, detail="shopify_order_id required")
    if channel_primary not in ALLOWED_ATTRIBUTION_CHANNELS:
        raise HTTPException(status_code=400, detail="invalid channel_primary")

    reason = (body.get("purchase_reason") or "").strip().lower() or None
    if reason and reason not in ALLOWED_PURCHASE_REASONS:
        reason = "other"

    # Optional anniversary capture (Phase 9.2). Only accepted when reason is
    # anniversary/engagement/milestone. Date must be ISO YYYY-MM-DD and within
    # a sane range (no 1930s, no far-future).
    anniversary_date: str | None = None
    relationship: str | None = None
    raw_date = (body.get("anniversary_date") or "").strip()
    if raw_date and reason in {"anniversary", "engagement", "milestone"}:
        try:
            parsed = datetime.strptime(raw_date, "%Y-%m-%d").date()
            if date(1970, 1, 1) <= parsed <= date.today().replace(year=date.today().year + 5):
                anniversary_date = parsed.isoformat()
                raw_rel = (body.get("relationship") or "").strip().lower()
                if raw_rel in {"wedding_anniversary", "engagement", "birthday", "milestone", "other"}:
                    relationship = raw_rel
                elif reason == "anniversary":
                    relationship = "wedding_anniversary"
                elif reason == "engagement":
                    relationship = "engagement"
                else:
                    relationship = "milestone"
        except ValueError:
            pass  # Invalid date — silently drop the field

    db = AsyncDatabase()

    # Validate the order exists (cheap spam filter — fake IDs won't match)
    try:
        order_id_int = int(shopify_order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="order not found")

    order = await db.get_order_by_shopify_id(order_id_int)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    row = {
        "shopify_order_id": shopify_order_id,
        "customer_email": (body.get("customer_email") or order.get("buyer_email") or "").strip().lower() or None,
        "channel_primary": channel_primary,
        "channel_detail": (body.get("channel_detail") or "").strip()[:500] or None,
        "purchase_reason": reason,
        "purchase_reason_detail": (body.get("purchase_reason_detail") or "").strip()[:500] or None,
        "anniversary_date": anniversary_date,
        "relationship": relationship,
        "submitted_via": "thankyou_page",
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500] or None,
        "raw_response": body,
    }

    try:
        await db.insert_attribution(row)
    except Exception as exc:
        # UNIQUE (shopify_order_id, submitted_via) → already recorded. Treat as success.
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            return {"status": "already_recorded"}
        logger.exception("Failed to insert attribution for order %s", shopify_order_id)
        raise HTTPException(status_code=500, detail="Could not record response")

    # If the buyer gave us an anniversary date, store it on customer_anniversaries
    # so the yearly reminder cron can fire. Non-fatal on failure.
    if anniversary_date and order.get("customer_id"):
        try:
            await db.upsert_customer_anniversary({
                "customer_id": order["customer_id"],
                "customer_email": row["customer_email"],
                "anniversary_date": anniversary_date,
                "relationship": relationship,
                "source_order_id": order.get("id"),
            })
        except Exception:
            logger.exception("Failed to upsert anniversary for order %s (non-fatal)", shopify_order_id)

    try:
        from src.agents.observations import observe
        summary = (
            f"Buyer on order #{shopify_order_id} said heard via {channel_primary}"
            + (f" ({row['channel_detail']})" if row["channel_detail"] else "")
            + (f" — {relationship} on {anniversary_date}" if anniversary_date else "")
        )
        await observe(
            source="survey:post_purchase",
            category="marketing",
            severity="info",
            summary=summary,
            entity_type="order",
            entity_id=shopify_order_id,
            data={
                "channel_primary": channel_primary,
                "channel_detail": row["channel_detail"],
                "purchase_reason": reason,
                "anniversary_date": anniversary_date,
                "relationship": relationship,
            },
        )
    except Exception:
        pass  # Observation failure non-fatal

    return {"status": "ok"}


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


@app.post("/webhook/shopify/products")
async def shopify_products_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle products/create + products/update webhooks. Same payload; upsert covers both."""
    return await handle_product_webhook(request, background_tasks)


@app.post("/webhook/shopify/products-delete")
async def shopify_products_delete_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle products/delete webhook. Payload is just {"id": N}."""
    return await handle_product_delete_webhook(request, background_tasks)


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
    """Check for orders needing crafting update emails (Day 2-3 post-order).

    AUTO-tier (Phase 12): copy is fully templated, no Claude, and reversible
    (we can always email a correction). Sends directly, logs to
    auto_sent_actions for founder review at /dashboard/agents/order_ops.
    """
    from src.agents.approval_tiers import log_auto_sent

    db = AsyncDatabase()
    email_sender = EmailSender()
    sent = 0
    failed = 0

    orders = await db.get_orders_needing_crafting_update(settings.crafting_update_delay_days)

    for order in orders:
        customer = order.get("customers") or {}
        customer_email = customer.get("email") or order.get("buyer_email", "")
        if not customer_email:
            continue

        customer_name = customer.get("name") or order.get("buyer_name") or customer_email
        order_created = order.get("created_at", "")
        order_id = order.get("shopify_order_id")
        order_number = str(order_id or "")

        days_since = 0
        if order_created:
            try:
                created = datetime.fromisoformat(order_created.replace("Z", ""))
                days_since = (datetime.utcnow() - created).days
            except (ValueError, TypeError):
                pass

        email_body = (
            f"Good news — we've started crafting your bracelet. "
            f"Day {days_since} of our 15-business-day handcraft process. "
            f"Each diamond is being hand-set under 10x magnification. "
            f"We'll share another update when your piece moves to final polishing."
        )

        try:
            ok = await asyncio.to_thread(
                email_sender.send_crafting_update,
                customer_email, customer_name, order_number, email_body,
            )
        except Exception:
            logger.exception("crafting_update send failed for order %s", order_number)
            failed += 1
            continue

        if not ok:
            failed += 1
            continue

        try:
            await db.update_order_status(int(order_id), "crafting_update_sent")
        except Exception:
            logger.exception("crafting_update: status update failed for %s", order_id)

        try:
            await log_auto_sent(
                agent_name="order_ops",
                action_type="crafting_update_email",
                entity_type="order",
                entity_id=order_number,
                payload={
                    "email": customer_email,
                    "days_since_order": days_since,
                    "body": email_body,
                },
            )
        except Exception:
            logger.exception("auto_sent log failed for crafting update %s", order_number)

        sent += 1

    return {"status": "ok", "module": "crafting_updates", "sent": sent, "failed": failed}


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


@app.post("/cron/competitor-brief", dependencies=[Depends(verify_cron_secret)])
async def cron_competitor_brief():
    """Weekly competitor intelligence brief — runs Monday 10 AM ET.

    Claude + native WebSearch tool surveys Vrai/Catbird/Mejuri/Aurate/Mateo
    and produces 3-5 sharp observations on messaging/offers/press moves.
    Not daily scraping — weekly synthesis is the honest scope at our budget.

    Cost: ~$0.15-0.25/run (WebSearch tool + Claude tokens).
    """
    from src.marketing.competitor_brief import CompetitorBrief

    try:
        brief = CompetitorBrief()
        result = await brief.run_weekly()
        return {
            "status": "ok",
            "observations": len(result.observations),
        }
    except Exception as e:
        logger.exception("Competitor brief failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/pinterest-pins", dependencies=[Depends(verify_cron_secret)])
async def cron_pinterest_pins():
    """Pinterest pin generator — runs Mon/Wed/Fri at 1 PM ET (3 pins/week).

    Picks one product per run, drafts Pinterest-optimized copy via Claude,
    creates pin via Pinterest API v5. No-op if PINTEREST_ACCESS_TOKEN or
    PINTEREST_BOARD_ID are missing.
    """
    from datetime import date as _date
    from src.marketing.pinterest import PinterestClient

    client = PinterestClient()
    if not client.is_configured:
        return {"status": "skipped", "reason": "Pinterest not configured"}

    # Stable day-based rotation — different product each day we run
    day_index = (_date.today() - _date(2026, 1, 1)).days

    product = await client.pick_product(day_index=day_index)
    if not product:
        return {"status": "error", "error": "No products to pin"}

    draft = await client.draft_copy(product)
    if not draft:
        return {"status": "error", "error": "Could not draft pin for product"}

    result = await client.create_pin(draft)

    slack = SlackNotifier()
    if result.pin_id:
        await slack.send_blocks([
            {"type": "header", "text": {"type": "plain_text", "text": ":pushpin: Pinterest pin created"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Product:* {result.product_name}"},
                {"type": "mrkdwn", "text": f"*Pin ID:* `{result.pin_id}`"},
                {"type": "mrkdwn", "text": f"*Title:* {result.title}"},
            ]},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"<https://pinterest.com/pin/{result.pin_id}|View pin>"}},
        ], text=f"Pin created: {result.product_name}")
    else:
        await slack.send_blocks([
            {"type": "header", "text": {"type": "plain_text", "text": ":warning: Pinterest pin failed"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*Product:* {result.product_name}\n*Error:* `{result.error}`"}},
        ], text=f"Pin failed: {result.product_name}")

    return {
        "status": "ok" if result.pin_id else "error",
        "product": result.product_name,
        "pin_id": result.pin_id,
        "error": result.error,
    }


@app.post("/cron/quarterly-poq", dependencies=[Depends(verify_cron_secret)])
async def cron_quarterly_poq():
    """Piece of the Quarter email — fires first Monday of Jan/Apr/Jul/Oct.

    Claude drafts a short email featuring one new piece. Posts to Slack
    with Approve & Send / Skip buttons. On approve, batch-sends to every
    past buyer who accepts marketing.
    """
    from src.customer.piece_of_quarter import PieceOfQuarter

    try:
        poq = PieceOfQuarter()
        draft = await poq.draft()
        slack = SlackNotifier()
        blocks = [
            {"type": "header", "text": {"type": "plain_text",
                "text": f":package: Piece of the Quarter — {draft.quarter_key}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Featured:* {draft.featured_piece}"},
                {"type": "mrkdwn", "text": f"*Audience:* {draft.audience_count} past buyers"},
                {"type": "mrkdwn", "text": f"*Subject:* {draft.subject}"},
            ]},
            {"type": "divider"},
            {"type": "section", "block_id": "poq_draft",
             "text": {"type": "mrkdwn", "text": f"*Draft:*\n{draft.body}"}},
            {"type": "divider"},
            {"type": "actions",
             "block_id": f"poq_review_{draft.quarter_key}",
             "elements": [
                {"type": "button",
                 "text": {"type": "plain_text", "text": f"Approve & Send to {draft.audience_count}"},
                 "style": "primary",
                 "action_id": "approve_poq",
                 "value": json.dumps({
                     "subject": draft.subject,
                     "quarter_key": draft.quarter_key,
                     "audience_count": draft.audience_count,
                 })},
                {"type": "button",
                 "text": {"type": "plain_text", "text": "Skip"},
                 "action_id": "skip_poq",
                 "value": draft.quarter_key},
             ]},
        ]
        await slack.send_blocks(blocks, text=f"POQ {draft.quarter_key}: {draft.subject}")
        return {
            "status": "ok",
            "quarter": draft.quarter_key,
            "subject": draft.subject,
            "audience_count": draft.audience_count,
            "featured": draft.featured_piece,
        }
    except Exception as e:
        logger.exception("POQ cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/seo-post", dependencies=[Depends(verify_cron_secret)])
async def cron_seo_post():
    """Weekly long-tail SEO journal post — runs Monday 2 PM ET.

    Picks the next-due keyword from seo_topics table. Claude drafts
    900-1,400 word post. If SHOPIFY_BLOG_ID + write_content scope are
    available, publishes as DRAFT and sends admin URL to Slack. Otherwise
    sends the full markdown to Slack for manual paste.
    """
    from src.content.seo_writer import SEOWriter

    try:
        writer = SEOWriter()
        await writer.ensure_topics_seeded()
        topic = await writer.next_topic()
        if not topic:
            return {"status": "error", "error": "No active SEO topics"}

        keyword = topic["keyword"]
        category = topic.get("category") or "general"

        draft = await writer.draft(keyword, category)
        shopify_enabled = writer.shopify_publish_enabled
        if shopify_enabled:
            draft = await writer.publish_draft(draft)

        await writer.mark_used(
            topic["id"],
            article_id=draft.shopify_article_id,
            admin_url=draft.shopify_admin_url,
        )

        # Slack notification
        slack = SlackNotifier()
        if draft.shopify_admin_url and not draft.publish_error:
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": ":writing_hand: Weekly SEO Draft"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Keyword:* {keyword}"},
                    {"type": "mrkdwn", "text": f"*Category:* {category}"},
                    {"type": "mrkdwn", "text": f"*Title:* {draft.title}"},
                    {"type": "mrkdwn", "text": f"*Words:* {draft.word_count}"},
                ]},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*Meta description:*\n{draft.meta_description}"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f":link: <{draft.shopify_admin_url}|Review draft in Shopify admin>"}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": "_Published as draft — review, tweak, click Publish in Shopify admin._"}]},
            ]
        else:
            # Fallback: paste-to-Shopify
            error_note = f" (API: {draft.publish_error})" if draft.publish_error else ""
            body_excerpt = draft.body_markdown[:2500]
            if len(draft.body_markdown) > 2500:
                body_excerpt += "\n\n_... (truncated — full post in logs)_"
            blocks = [
                {"type": "header", "text": {"type": "plain_text",
                    "text": ":writing_hand: Weekly SEO Draft (paste mode)"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Keyword:* {keyword}"},
                    {"type": "mrkdwn", "text": f"*Category:* {category}"},
                    {"type": "mrkdwn", "text": f"*Title:* {draft.title}"},
                    {"type": "mrkdwn", "text": f"*Slug:* `{draft.slug}`"},
                ]},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*Meta description:* {draft.meta_description}"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*Tags:* `{', '.join(draft.tags)}`"}},
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"```\n{body_excerpt}\n```"}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f":warning: Shopify auto-publish disabled{error_note}. "
                            f"Paste into Admin → Online Store → Blog posts."}]},
            ]
            # Log full body so founder can retrieve if Slack truncates
            logger.info("SEO FULL BODY for %s:\n%s", keyword, draft.body_markdown)

        await slack.send_blocks(blocks, text=f"SEO draft: {draft.title}")

        return {
            "status": "ok",
            "keyword": keyword,
            "title": draft.title,
            "word_count": draft.word_count,
            "shopify_article_id": draft.shopify_article_id,
            "shopify_admin_url": draft.shopify_admin_url,
            "publish_error": draft.publish_error,
        }
    except Exception as e:
        logger.exception("SEO post cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/reconcile-products", dependencies=[Depends(verify_cron_secret)])
async def cron_reconcile_products():
    """Daily Shopify→Supabase product reconciliation — runs 6:30 AM ET.

    Defence-in-depth for product webhooks. If a products/create, /update,
    or /delete webhook misses (Shopify delivery failure, our service down),
    this cron catches up.

    Pulls every Shopify product, upserts into Supabase, deletes Supabase
    rows whose shopify_product_id no longer exists in Shopify.
    """
    from src.core.shopify_sync import reconcile_products
    try:
        result = await reconcile_products(delete_missing=True)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("reconcile-products cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/reconcile-customers", dependencies=[Depends(verify_cron_secret)])
async def cron_reconcile_customers():
    """Daily Shopify→Supabase customer reconciliation — runs 5 AM ET.

    Catches up on any customer create/update webhooks that missed. Does NOT
    delete missing rows (GDPR handling is an explicit flow, not a silent
    reconciliation).
    """
    from src.core.shopify_sync import reconcile_customers
    try:
        result = await reconcile_customers()
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("reconcile-customers cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/reconcile-meta-ads", dependencies=[Depends(verify_cron_secret)])
async def cron_reconcile_meta_ads():
    """Daily Meta→Supabase ad status reverse-sync — runs 9 AM ET.

    Founder pauses/resumes ads directly in Meta Ads Manager. Without this,
    ad_creatives.status drifts and fatigue detection lies.
    """
    from src.marketing.meta_ad_sync import reconcile_ad_statuses
    try:
        result = await reconcile_ad_statuses()
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("reconcile-meta-ads cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/verify-outcomes", dependencies=[Depends(verify_cron_secret)])
async def cron_verify_outcomes():
    """Daily — run program-verified outcome checks across all agents.

    Scans recent orders for on-time/late shipping, recent auto-sent emails
    for customer replies within 48h, retention emails for repurchase within
    30d. Deterministic SQL checks only — no LLM scoring. Idempotent via
    keys on each outcome row, so re-runs are safe.

    Scheduled 4:30 AM ET (after KPI compute at 4 AM).
    """
    from src.agents.outcomes import verify_all
    try:
        results = await verify_all()
        return {"status": "ok", **results}
    except Exception as e:
        logger.exception("verify-outcomes cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/webhook/sendgrid")
async def webhook_sendgrid(request: Request):
    """SendGrid event webhook — captures delivery / open / click / bounce.

    Signature verification (Phase 13.3 hardening): if
    SENDGRID_WEBHOOK_PUBLIC_KEY is set, we verify ECDSA-SHA256 over
    `X-Twilio-Email-Event-Webhook-Timestamp + raw_body` using the PEM/DER
    public key copied from SendGrid admin. Missing or invalid signature
    on a configured env returns 401.

    If the env var is NOT set, we accept unsigned with a startup-time
    warning (URL-obscurity-only, pre-hardening behavior). Set the key
    on Railway to flip on verification without changing code.

    Payload is a JSON array of events. Idempotency handled downstream
    via sg_event_id.
    """
    import json as _json
    from src.agents.outcomes import record_sendgrid_events, verify_sendgrid_signature

    body = await request.body()

    # Verify if a public key is configured
    if settings.sendgrid_webhook_public_key:
        signature = request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
        timestamp = request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")
        if not verify_sendgrid_signature(
            body, signature, timestamp, settings.sendgrid_webhook_public_key,
        ):
            logger.warning("sendgrid webhook: signature verification failed")
            raise HTTPException(status_code=401, detail="invalid signature")
    else:
        logger.debug("sendgrid webhook: accepting unsigned (SENDGRID_WEBHOOK_PUBLIC_KEY not set)")

    try:
        events = _json.loads(body) if body else []
        if not isinstance(events, list):
            return {"status": "error", "error": "expected JSON array"}
        result = await record_sendgrid_events(events)
        return {"status": "ok", **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("sendgrid webhook failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/compile-entity-memory", dependencies=[Depends(verify_cron_secret)])
async def cron_compile_entity_memory():
    """Nightly — compile markdown wikis for active customers + products + current/next month.

    Runs 3:30 AM ET (before the 4 AM KPI cron so retros / dashboards can
    use fresh notes). Skips entities with a note <24h old unless raw data
    has moved past source_through. Bounded by our current active volume —
    a full run is O(customers + products), not O(all history).
    """
    from src.agents.memory import compile_all_active
    try:
        results = await compile_all_active()
        return {"status": "ok", **results}
    except Exception as e:
        logger.exception("compile-entity-memory cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/compute-agent-kpis", dependencies=[Depends(verify_cron_secret)])
async def cron_compute_agent_kpis():
    """Compute each agent's north-star KPI and store in agent_kpis.

    Runs daily at 4 AM ET (before the 5 AM customer reconcile so dashboards
    have fresh numbers by the time founder checks them).
    """
    from src.agents.kpis import compute_all
    try:
        results = await compute_all()
        return {"status": "ok", "results": results}
    except Exception as e:
        logger.exception("compute-agent-kpis cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/weekly-agent-retros", dependencies=[Depends(verify_cron_secret)])
async def cron_weekly_agent_retros():
    """Each agent writes a 2-para self-review of the last week.

    Monday 8 AM ET. Saves to agent_retros and posts a combined summary
    to Slack. Founder reads 5 retros instead of reviewing 100 actions.
    """
    from src.agents.retros import generate_weekly_retros
    try:
        results = await generate_weekly_retros()
        return {"status": "ok", "agents_reviewed": len(results)}
    except Exception as e:
        logger.exception("weekly-agent-retros cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/tier-audit", dependencies=[Depends(verify_cron_secret)])
async def cron_tier_audit():
    """Weekly — review AUTO vs REVIEW tier calibration (Phase 12.5c).

    Surfaces evidence to observations table (consumed by heartbeat → Slack).
    Never auto-mutates AUTO_ACTIONS — founder makes the call.

    Schedule: Sun 10 PM ET (before roll_founder_style which runs Sun 11 PM).
    """
    from src.agents.tier_audit import run_audit
    try:
        result = await run_audit()
        return result
    except Exception as e:
        logger.exception("tier-audit cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/roll-founder-style", dependencies=[Depends(verify_cron_secret)])
async def cron_roll_founder_style():
    """Summarize founder edits from approval_feedback into per-agent style guidance.

    Sundays 11 PM ET. For any trigger with 10+ un-incorporated edits, Claude
    summarizes the editing pattern and appends to that agent's prompt context.
    """
    from src.agents.feedback_loop import roll_founder_style
    try:
        results = await roll_founder_style()
        return {"status": "ok", **results}
    except Exception as e:
        logger.exception("roll-founder-style cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/reconcile-seo-publish", dependencies=[Depends(verify_cron_secret)])
async def cron_reconcile_seo_publish():
    """Weekly Shopify blog publish reverse-sync — runs Friday 10 AM ET.

    Drafts we push to Shopify get reviewed and published from admin. This
    mirrors `published_at` back into seo_topics and fires a Slack celebration
    for newly-published posts.
    """
    from src.content.seo_publish_sync import reconcile_seo_publish_status
    try:
        result = await reconcile_seo_publish_status()
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("reconcile-seo-publish cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/weekly-creative-rotation", dependencies=[Depends(verify_cron_secret)])
async def cron_weekly_creative_rotation():
    """Weekly rotation — picks the stalest active product and generates 3 fresh variants.

    Runs Monday 1 PM ET. Prevents stale ads from running past 14 days with
    outdated brand copy. Uses Phase 10.E closed-loop insights (top-performers
    from last 30d) to make each generation smarter than the last.

    Safeguards:
      - Skips if AUTO_ROTATE_CREATIVES=false on Railway
      - Skips if >5 creatives already pending_review (back-pressure)
      - Skips if no product is stale enough (everything was rotated recently)
      - Max 1 product per run
    """
    import os
    import uuid as _uuid

    if os.environ.get("AUTO_ROTATE_CREATIVES", "true").lower() in ("false", "0", "off"):
        return {"status": "disabled", "reason": "AUTO_ROTATE_CREATIVES env flag set to false"}

    from src.marketing.ad_generator import AdCreativeGenerator, AdGeneratorError, fetch_top_performers

    db = AsyncDatabase()

    # Back-pressure check
    pending = await db.count_pending_ad_creatives()
    if pending > 5:
        logger.info("Rotation: %d pending approvals, skipping to avoid pile-up", pending)
        return {"status": "skipped", "reason": "too_many_pending", "pending": pending}

    # Pick the stalest product
    product = await db.get_next_rotation_sku(min_days_stale=14)
    if not product:
        return {"status": "skipped", "reason": "no_stale_products"}

    sku = product["sku"]
    last_generated = product.get("_last_creative_at") or "never"

    # Pull closed-loop insights (Phase 10.E)
    top_performers = await fetch_top_performers(db, days=30, limit=5)

    try:
        gen = AdCreativeGenerator()
        variants, batch_id, dna_hash = gen.generate(
            product, n_variants=3, top_performers=top_performers,
        )
    except AdGeneratorError as e:
        logger.error("Rotation: generation failed for %s: %s", sku, e)
        return {"status": "error", "error": str(e), "sku": sku}

    rows = [
        v.to_db_row(sku=sku, generation_batch_id=batch_id, brand_dna_hash=dna_hash)
        for v in variants
    ]
    await db.create_ad_creative_batch(rows)

    # Slack notification + observation
    slack = SlackNotifier()
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": ":arrows_counterclockwise: Weekly Creative Rotation"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Product:* `{sku}`"},
            {"type": "mrkdwn", "text": f"*Name:* {product.get('name', '—')}"},
            {"type": "mrkdwn", "text": f"*Last generated:* {last_generated[:10] if last_generated != 'never' else 'never'}"},
            {"type": "mrkdwn", "text": f"*Variants drafted:* {len(variants)}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": (
                f":point_right: <https://pinaka-agents-production-198b5.up.railway.app/dashboard/ad-creatives"
                f"|Review the 3 variants>" + (
                    f" · *{len(top_performers)} top performers* informed the prompt"
                    if top_performers else " · no historical performers yet — first batch"
                )
            )}},
    ]
    await slack.send_blocks(blocks, text=f"Weekly rotation: {sku}")

    try:
        from src.agents.observations import observe
        await observe(
            source="cron:weekly_creative_rotation",
            category="marketing",
            severity="info",
            summary=f"Weekly rotation generated {len(variants)} variants for {sku} (last: {last_generated[:10] if last_generated != 'never' else 'never'})",
            entity_type="ad_creative_batch",
            entity_id=batch_id,
            data={"sku": sku, "batch_id": batch_id, "top_performers": len(top_performers)},
        )
    except Exception:
        pass

    return {
        "status": "ok",
        "sku": sku,
        "batch_id": batch_id,
        "variant_count": len(variants),
        "last_generated": last_generated,
        "top_performers_used": len(top_performers),
    }


@app.post("/cron/rfm-compute", dependencies=[Depends(verify_cron_secret)])
async def cron_rfm_compute():
    """Daily RFM scoring for all past buyers — runs 8 AM ET.

    Upserts one row per customer per day into customer_rfm. Updates
    customers.last_segment + customers.last_rfm_at so the dashboard brief
    can read segment counts without joining.
    """
    from src.customer.rfm import RFMScorer

    try:
        scorer = RFMScorer()
        result = await scorer.run_daily()
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("RFM compute cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/voc-mine", dependencies=[Depends(verify_cron_secret)])
async def cron_voc_mine():
    """Weekly voice-of-customer theme miner — runs Monday 11 AM ET.

    Claude clusters last 7 days of customer text (emails, chats, surveys)
    into 3-5 themes. Posts to Slack + writes one row per week to
    customer_insights.
    """
    from src.customer.voc import VoiceOfCustomer

    try:
        voc = VoiceOfCustomer()
        result = await voc.run_weekly()
        return {
            "status": "ok",
            "week_ending": result.week_ending.isoformat(),
            "themes": len(result.themes),
            "messages_analyzed": result.messages_analyzed,
            "chats_analyzed": result.chats_analyzed,
            "survey_responses": result.survey_responses,
        }
    except Exception as e:
        logger.exception("VOC mine cron failed")
        return {"status": "error", "error": str(e)}


@app.get("/api/customer/{customer_id}/profile")
async def get_customer_profile(customer_id: int, request: Request):
    """Unified customer profile (Phase 10.A). Dashboard-authenticated.

    Joins orders, messages, attribution, anniversaries, lifecycle state,
    and latest RFM snapshot. Read-only. Cached for 60s on the client (ETag).
    """
    # Reuse dashboard auth — the dash_token cookie guards all /api/customer/*
    from src.dashboard.web import _check_auth
    dash_token = request.cookies.get("dash_token")
    if not _check_auth(dash_token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    from src.customer.profile import CustomerProfileBuilder
    try:
        builder = CustomerProfileBuilder()
        profile = await builder.for_customer(customer_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Customer not found")
        return builder.to_json(profile)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Customer profile build failed for %d", customer_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cron/lifecycle-daily", dependencies=[Depends(verify_cron_secret)])
async def cron_lifecycle_daily():
    """Post-purchase lifecycle orchestrator — runs daily 10 AM ET.

    Finds candidates for care_guide_day10, referral_day60, custom_inquiry_day180,
    and anniversary_year1 triggers. Claude drafts each. Posts to Slack for
    founder approval; Slack handler sends on approve. Dedupes per-customer +
    per-trigger via customers.lifecycle_emails_sent.
    """
    from src.customer.lifecycle import LifecycleOrchestrator

    try:
        orch = LifecycleOrchestrator()
        candidates = await orch.find_all_candidates()
        slack = SlackNotifier()
        posted = 0
        for cand in candidates[:20]:  # cap per run — avoid Slack spam if DB has a backlog
            drafted = await orch.draft(cand)
            context = ""
            if cand.anniversary_date:
                context = f"{cand.relationship} on {cand.anniversary_date}"
            elif cand.days_since_purchase is not None:
                context = f"{cand.days_since_purchase}d since #{cand.last_order_number}"
            try:
                await slack.send_lifecycle_email_review(
                    customer_name=cand.customer_name,
                    customer_email=cand.customer_email,
                    customer_id=cand.customer_id,
                    trigger=cand.trigger,
                    subject=drafted.subject,
                    email_body=drafted.body,
                    context_note=context,
                    anniversary_id=cand.anniversary_id,
                    anniversary_year_key=cand.anniversary_year_key,
                )
                posted += 1
            except Exception:
                logger.exception("Failed to post lifecycle review for %s / %s",
                                 cand.customer_email, cand.trigger)
        return {
            "status": "ok",
            "candidates_found": len(candidates),
            "slack_posts": posted,
            "by_trigger": {
                t: sum(1 for c in candidates if c.trigger == t)
                for t in {c.trigger for c in candidates}
            },
        }
    except Exception as e:
        logger.exception("Lifecycle cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/welcome-daily", dependencies=[Depends(verify_cron_secret)])
async def cron_welcome_daily():
    """Welcome educational series — runs daily 11 AM ET.

    For each customer in the welcome cohort (welcome_started_at set, 0 orders),
    sends the next due step based on days elapsed. No Slack approval — these
    are pre-vetted static templates. 5 emails over 18 days: day 0, 3, 7, 12, 18.
    """
    from src.customer.welcome import WelcomeSeriesEngine

    try:
        engine = WelcomeSeriesEngine()
        result = await engine.send_due()
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("Welcome daily cron failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/ugc-brief", dependencies=[Depends(verify_cron_secret)])
async def cron_ugc_brief():
    """Weekly UGC filming brief — runs Sunday 6 PM ET.

    Claude generates 3 phone-shot video prompts for the founder to film during
    the week. Each brief: setup, hook line, 3 script beats, why-it-works. Uses
    seasonal calendar + recent products + top-spending ad name as context.

    One Claude call (~$0.02/run). Cheap weekly, not daily.
    """
    from src.marketing.ugc_brief import UGCBriefGenerator

    try:
        gen = UGCBriefGenerator()
        result = await gen.run_weekly()
        return {
            "status": "ok",
            "briefs_generated": len(result.briefs),
            "seasonal_window": result.seasonal_window,
            "top_archetype": result.top_archetype_last_14d,
        }
    except Exception as e:
        logger.exception("UGC brief generation failed")
        return {"status": "error", "error": str(e)}


@app.post("/cron/creative-health", dependencies=[Depends(verify_cron_secret)])
async def cron_creative_health():
    """Daily creative fatigue check. Posts Slack alerts for any ad that's
    decaying. Runs 9 AM ET after sync-creative-metrics.

    Rules in src/marketing/creative_fatigue.py — dead_spend, high_freq,
    ctr_decay, weak_ctr. At most one flag per ad. New/low-volume ads
    (<500 impressions) are skipped to avoid false positives.
    """
    from zoneinfo import ZoneInfo
    from src.marketing.creative_fatigue import detect_fatigue
    from src.agents.observations import observe

    tz = ZoneInfo(settings.business_timezone)
    today = datetime.now(tz).date()
    db = AsyncDatabase()

    rows = await db.get_creative_metrics_range(today - timedelta(days=14), today)
    flags = detect_fatigue(rows, today)

    if not flags:
        logger.info("Creative health: no fatigue detected across %d rows", len(rows))
        return {"status": "ok", "flags": 0, "rows_analyzed": len(rows)}

    # Group by reason for Slack summary
    reason_icon = {
        "dead_spend": ":fire:",
        "high_freq": ":repeat:",
        "ctr_decay": ":chart_with_downwards_trend:",
        "weak_ctr": ":zzz:",
    }
    reason_label = {
        "dead_spend": "Dead spend",
        "high_freq": "High frequency",
        "ctr_decay": "CTR decay",
        "weak_ctr": "Weak CTR",
    }

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": ":broken_heart: Creative Fatigue Detected"}},
        {"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": f"*{len(flags)} ad(s)* need attention. "
                    f"Generate replacements at /dashboard/ad-creatives.",
        }]},
        {"type": "divider"},
    ]

    for flag in flags[:10]:  # cap at 10 per alert — more means something bigger is wrong
        icon = reason_icon.get(flag.reason, ":warning:")
        label = reason_label.get(flag.reason, flag.reason)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{icon} *{label}* — `{flag.ad_name or flag.meta_ad_id}`\n"
                    f"{flag.detail}"
                ),
            },
        })

    slack = SlackNotifier()
    await slack.send_blocks(blocks, text=f"Creative fatigue: {len(flags)} ad(s) need attention")

    # Write observation per flag for heartbeat awareness
    for flag in flags:
        await observe(
            source="cron:creative_health",
            category="marketing",
            severity="warning" if flag.reason == "dead_spend" else "info",
            summary=f"Creative fatigue ({flag.reason}): {flag.ad_name or flag.meta_ad_id} — {flag.detail}",
            entity_type="ad_creative",
            entity_id=flag.meta_ad_id,
            data={"reason": flag.reason, "metrics": flag.metrics, "creative_id": flag.meta_creative_id},
        )

    return {
        "status": "ok",
        "flags": len(flags),
        "rows_analyzed": len(rows),
        "by_reason": {r: sum(1 for f in flags if f.reason == r) for r in reason_icon},
    }


@app.post("/cron/sync-creative-metrics", dependencies=[Depends(verify_cron_secret)])
async def cron_sync_creative_metrics():
    """Pull yesterday's per-ad metrics from Meta Insights (level=ad) and upsert
    into ad_creative_metrics. Daily 7 AM ET (after sync-ad-spend).

    Feeds creative fatigue detector + per-creative breakdown in weekly report.
    """
    from zoneinfo import ZoneInfo
    from src.marketing.meta_ads import MetaAdsClient, MetaAdsError

    tz = ZoneInfo(settings.business_timezone)
    yesterday = datetime.now(tz).date() - timedelta(days=1)

    meta = MetaAdsClient()
    if not meta.is_configured:
        return {"status": "skipped", "reason": "Meta Ads not configured"}

    try:
        insights = await meta.get_creative_insights(yesterday)
    except MetaAdsError as e:
        logger.error("Creative insights pull failed: %s", e)
        return {"status": "error", "error": str(e)}

    db = AsyncDatabase()
    rows_written = 0
    for ins in insights:
        try:
            await db.upsert_creative_metrics({
                "date": ins.date.isoformat(),
                "meta_ad_id": ins.meta_ad_id,
                "meta_creative_id": ins.meta_creative_id,
                "meta_adset_id": ins.meta_adset_id,
                "meta_campaign_id": ins.meta_campaign_id,
                "ad_name": ins.ad_name,
                "creative_name": ins.creative_name,
                "impressions": ins.impressions,
                "reach": ins.reach,
                "clicks": ins.clicks,
                "spend": ins.spend,
                "ctr": ins.ctr,
                "cpm": ins.cpm,
                "cpc": ins.cpc,
                "frequency": ins.frequency,
                "view_content_count": ins.view_content_count,
                "atc_count": ins.atc_count,
                "ic_count": ins.ic_count,
                "purchase_count": ins.purchase_count,
                "purchase_value": ins.purchase_value,
                "raw": ins.raw,
            })
            rows_written += 1
        except Exception:
            logger.exception("Failed to upsert metrics for ad %s", ins.meta_ad_id)

    return {
        "status": "ok",
        "date": yesterday.isoformat(),
        "ads_with_data": len(insights),
        "rows_written": rows_written,
    }


@app.post("/cron/attribution-synthesize", dependencies=[Depends(verify_cron_secret)])
async def cron_attribution_synthesize():
    """Weekly post-purchase survey synthesizer — runs Monday 9:30 AM ET.

    Aggregates the last 7 days of post_purchase_attribution responses, clusters
    free-text details via Claude, and posts a ground-truth attribution report
    to Slack. This overrides Meta/GA4 last-click for budget reallocation.
    """
    from src.marketing.attribution_synth import AttributionSynthesizer

    try:
        synth = AttributionSynthesizer()
        result = await synth.run_weekly_report(window_days=7)
        return {
            "status": "ok",
            "total_responses": result.total_responses,
            "channels": result.channel_counts,
            "observations": len(result.ai_observations),
        }
    except Exception as e:
        logger.exception("Attribution synthesizer failed")
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
    """Handle Slack Block Kit interactivity — button clicks AND modal submits.

    Phase 12.5 (commit 4aa4fe2 + this): a button with action_id starting
    `edit_*` now opens a pre-filled modal. When founder submits the modal,
    Slack posts a `view_submission` payload back here — we capture the
    diff to `approval_feedback` and then run the corresponding approve_*
    path with the edited text, so the customer still gets an email.
    """
    body = await request.body()
    await _verify_slack_request(request, body)

    form_data = await request.form()
    payload = json.loads(form_data.get("payload", "{}"))

    payload_type = payload.get("type", "")
    if payload_type == "view_submission":
        return await _handle_slack_modal_submit(payload)

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

    # ── Edit buttons: open a pre-filled modal instead of the old dismiss ──
    if action_id in ("edit_response", "edit_cart_recovery",
                     "edit_crafting_update", "edit_listing"):
        trigger_id = payload.get("trigger_id", "")
        try:
            original = await _load_draft_text_for_edit(action_id, value, payload)
        except Exception:
            logger.exception("Failed to load draft for %s", action_id)
            original = ""
        try:
            await slack.open_edit_modal(trigger_id, action_id, value, original,
                                         channel=channel, message_ts=message_ts)
        except Exception:
            logger.exception("Failed to open edit modal for %s", action_id)
        return {"status": "ok"}

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
                customer_id=target.get("customer_id"),
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
                customer_id=cart.get("customer_id"),
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
                customer_id=customer_id,
                interval_days=reorder_data.get("interval_days"),
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

    elif action_id == "approve_lifecycle":
        try:
            data = json.loads(value)
        except Exception:
            data = {}
        customer_id = data.get("customer_id")
        customer_email = data.get("customer_email", "")
        customer_name = data.get("customer_name", "Customer")
        trigger = data.get("trigger", "")
        subject = data.get("subject", "From Pinaka")
        anniversary_id = data.get("anniversary_id")
        anniversary_year_key = data.get("anniversary_year_key")

        # Extract the body from the Slack message blocks (same pattern as reorder)
        body_text = ""
        for block in payload.get("message", {}).get("blocks", []):
            if block.get("block_id") == "lifecycle_draft":
                raw = block.get("text", {}).get("text", "")
                # Strip leading "*Draft:*\n" prefix the reviewer block adds
                body_text = raw.replace("*Draft:*\n", "", 1).strip()
                break

        ok = False
        if customer_email and body_text:
            ok = email.send_lifecycle_email(
                to_email=customer_email,
                customer_name=customer_name,
                subject=subject,
                email_body=body_text,
                customer_id=customer_id,
                trigger=trigger or "lifecycle_email",
            )

        if ok and customer_id and trigger:
            try:
                await asyncio.to_thread(
                    db._sync.mark_lifecycle_email_sent, int(customer_id), trigger,
                )
            except Exception:
                logger.exception("Failed to mark lifecycle sent for %s/%s", customer_id, trigger)

        # Mark anniversary reminded for this year even on failure — retry logic can handle
        if anniversary_id and anniversary_year_key:
            try:
                await asyncio.to_thread(
                    db._sync.mark_anniversary_reminded,
                    int(anniversary_id), anniversary_year_key,
                )
            except Exception:
                logger.exception("Failed to mark anniversary reminded %s/%s",
                                 anniversary_id, anniversary_year_key)

        label = "Lifecycle Sent" if ok else "Lifecycle Send FAILED"
        tombstone = SlackNotifier.tombstone_blocks(
            label, f"{customer_name} ({customer_email}) — {trigger}", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "approve_poq":
        try:
            data = json.loads(value)
        except Exception:
            data = {}
        subject = data.get("subject", "Piece of the Quarter")
        quarter_key = data.get("quarter_key", "")

        body_text = ""
        for block in payload.get("message", {}).get("blocks", []):
            if block.get("block_id") == "poq_draft":
                raw = block.get("text", {}).get("text", "")
                body_text = raw.replace("*Draft:*\n", "", 1).strip()
                break

        result = {"sent": 0, "audience": 0, "failed": 0}
        if body_text:
            from src.customer.piece_of_quarter import PieceOfQuarter
            try:
                result = await PieceOfQuarter().send_batch(subject=subject, body=body_text)
            except Exception:
                logger.exception("POQ batch send failed")

        label = f"POQ Sent ({result['sent']}/{result['audience']})"
        tombstone = SlackNotifier.tombstone_blocks(
            label, f"{quarter_key} — {subject}", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_poq":
        tombstone = SlackNotifier.tombstone_blocks(
            "POQ Skipped", f"Quarter {value}", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    elif action_id == "skip_lifecycle":
        try:
            data = json.loads(value)
        except Exception:
            data = {}
        customer_id = data.get("customer_id")
        trigger = data.get("trigger", "")
        anniversary_id = data.get("anniversary_id")
        anniversary_year_key = data.get("anniversary_year_key")

        # Skip = mark sent so we don't re-ask (user actively chose NOT to send)
        if customer_id and trigger:
            try:
                await asyncio.to_thread(
                    db._sync.mark_lifecycle_email_sent, int(customer_id), trigger,
                )
            except Exception:
                pass
        if anniversary_id and anniversary_year_key:
            try:
                await asyncio.to_thread(
                    db._sync.mark_anniversary_reminded,
                    int(anniversary_id), anniversary_year_key,
                )
            except Exception:
                pass

        tombstone = SlackNotifier.tombstone_blocks(
            "Lifecycle Skipped", f"Customer #{customer_id} — {trigger}", timestamp,
        )
        await slack.update_message(channel, message_ts, tombstone)

    # ── Dismiss Actions ──
    # edit_* actions are now handled at the top of this handler (modal open),
    # not here. Dismiss is a real no-op tombstone.
    elif action_id in ("dismiss", "contact_customer_exception"):
        tombstone = SlackNotifier.tombstone_blocks("Dismissed", action_id, timestamp)
        await slack.update_message(channel, message_ts, tombstone)

    else:
        logger.warning("Unknown Slack action: %s", action_id)

    return {"status": "ok"}


# ── Phase 12.5a — Slack edit modal plumbing ──

_EDIT_ACTION_TO_TRIGGER = {
    "edit_response": "customer_response",
    "edit_cart_recovery": "cart_recovery",
    "edit_crafting_update": "crafting_update",
    "edit_listing": "listing_publish",
}


async def _load_draft_text_for_edit(
    action_id: str, value: str, payload: dict[str, Any],
) -> str:
    """Fetch the original draft text so we can prefill the edit modal AND
    diff against it once the founder submits. Falls back to whatever text
    block is in the Slack message itself — better to pre-fill something
    than nothing, and the diff-capture ignores equal strings anyway."""
    db = AsyncDatabase()
    if action_id == "edit_response":
        rows = await db.get_pending_messages()
        target = next((m for m in rows if str(m.get("id")) == str(value)), None)
        if target and target.get("ai_draft"):
            return target["ai_draft"]
    elif action_id == "edit_listing":
        try:
            draft = await asyncio.to_thread(db._sync.get_listing_draft, int(value))
            if draft:
                # Return description as the editable portion
                return draft.get("description") or draft.get("body") or ""
        except Exception:
            logger.exception("get_listing_draft failed for %s", value)
    # For cart_recovery + crafting_update the text in the Slack block IS the
    # draft; fallback reads it back from the posted message.
    msg_blocks = payload.get("message", {}).get("blocks", []) or []
    for blk in msg_blocks:
        txt = (blk.get("text") or {}).get("text", "")
        # Heuristic: the draft is the longest section block text
        if blk.get("type") == "section" and len(txt) > 80:
            return txt.replace("*Draft:*\n", "").strip()
    return ""


async def _handle_slack_modal_submit(payload: dict[str, Any]) -> dict[str, str]:
    """View submission — founder finished editing a draft. Capture the diff
    and then run the corresponding approve path with the edited text so the
    action still happens."""
    view = payload.get("view", {}) or {}
    callback_id = view.get("callback_id", "")
    if not callback_id.startswith("modal_"):
        logger.warning("unknown modal callback_id %s", callback_id)
        return {"status": "no_action"}

    action_id = callback_id.replace("modal_", "")  # e.g. edit_response
    trigger_type = _EDIT_ACTION_TO_TRIGGER.get(action_id)
    if not trigger_type:
        logger.warning("modal for unknown edit action %s", action_id)
        return {"status": "no_action"}

    try:
        metadata = json.loads(view.get("private_metadata") or "{}")
    except Exception:
        metadata = {}
    value = metadata.get("value", "")
    original_text = metadata.get("original_text", "")
    channel = metadata.get("channel", "")
    message_ts = metadata.get("message_ts", "")

    state_values = (view.get("state") or {}).get("values") or {}
    block = state_values.get("edited_text_block") or {}
    edited_text = ((block.get("edited_text") or {}).get("value") or "").strip()

    # 1) Capture the edit for 12.5 style learning (fire-and-log)
    try:
        from src.agents.feedback_loop import capture_edit
        await capture_edit(
            agent_name="customer_service" if action_id == "edit_response"
                       else "retention" if action_id == "edit_cart_recovery"
                       else "order_ops" if action_id == "edit_crafting_update"
                       else "listings",
            trigger_type=trigger_type,
            original_text=original_text,
            edited_text=edited_text,
            context={"value": str(value), "source": "slack_modal"},
        )
    except Exception:
        logger.exception("capture_edit failed for %s/%s", action_id, value)

    # 2) Run the matching approve path with the edited text
    slack = SlackNotifier()
    db = AsyncDatabase()
    email = EmailSender()

    try:
        if action_id == "edit_response":
            msg = await db.get_pending_messages()
            target = next((m for m in msg if str(m.get("id")) == str(value)), None)
            if target:
                email.send_service_reply(
                    to_email=target.get("customer_email", ""),
                    customer_name=target.get("buyer_name", ""),
                    subject=target.get("subject", "Re: Your inquiry"),
                    email_body=edited_text,
                    customer_id=target.get("customer_id"),
                )
                await db.update_message_status(target["id"], "sent", human_approved=True)
        elif action_id == "edit_cart_recovery":
            cart = await db.get_cart_by_id(int(value))
            if cart:
                items_json = cart.get("items_json", "[]")
                items = json.loads(items_json) if isinstance(items_json, str) else items_json
                email.send_cart_recovery(
                    to_email=cart.get("customer_email", ""),
                    customer_name=cart.get("customer_email", "").split("@")[0],
                    cart_items=[i.get("title", "Item") for i in items],
                    cart_value=float(cart.get("cart_value", 0)),
                    customer_id=cart.get("customer_id"),
                )
                await db.upsert_cart_event({
                    "shopify_checkout_token": cart["shopify_checkout_token"],
                    "recovery_email_status": "sent",
                })
        elif action_id == "edit_crafting_update":
            order = await db.get_order_by_shopify_id(int(value))
            if order:
                email.send_crafting_update(
                    to_email=order.get("buyer_email", ""),
                    customer_name=order.get("buyer_name", "") or order.get("buyer_email", ""),
                    order_number=str(value),
                    email_body=edited_text,
                )
                await db.update_order_status(int(value), "crafting_update_sent")
        elif action_id == "edit_listing":
            # Listing edits are content-only; actual publish still flows through
            # the existing approve_listing handler. For now, log the edit and
            # tombstone. Full publish-with-edit can be wired when founder
            # actually uses this path.
            logger.info("edit_listing captured for %s; use Approve to publish",
                        value)
    except Exception:
        logger.exception("modal submit action failed for %s/%s", action_id, value)

    # Tombstone the original Slack message
    if channel and message_ts:
        try:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            tombstone = SlackNotifier.tombstone_blocks(
                "Edited & Sent", f"{action_id} #{value}", timestamp,
            )
            await slack.update_message(channel, message_ts, tombstone)
        except Exception:
            logger.exception("failed to tombstone after modal submit")

    # Slack requires a 200 with empty body (or response_action) to close modal
    return {"status": "ok"}
