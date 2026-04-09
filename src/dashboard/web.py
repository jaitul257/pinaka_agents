"""Pinaka Admin Dashboard — HTML pages served from FastAPI.

Product catalog management, password-gated. Styled per DESIGN.md.
"""

import base64
import hmac
import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Cookie, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core.database import Database
from src.core.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_db = None

def _get_db():
    global _db
    if _db is None:
        _db = Database()
    return _db

# ── Auth ──

def _check_auth(token: str | None) -> bool:
    """Verify dashboard auth cookie."""
    if not settings.dashboard_password:
        return True  # No password = dev mode
    if not token:
        return False
    return hmac.compare_digest(token, settings.dashboard_password)


# ── Shared HTML ──

def _base_html(title: str, body: str, active: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} — Pinaka</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600&family=DM+Sans:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --bg: #FAF7F2; --surface: #FFFFFF; --surface-raised: #F5F0E8;
            --text-primary: #2C2825; --text-secondary: #6B6560; --text-muted: #9E9893;
            --accent: #D4A017; --accent-hover: #B8890F; --accent-subtle: rgba(212,160,23,0.12);
            --gold: #C5A55A; --border: #E8E2D9; --border-light: #F0EBE3;
            --success: #2E7D4F; --success-bg: rgba(46,125,79,0.08);
            --error: #C4392D; --error-bg: rgba(196,57,45,0.08);
        }}
        body {{ background: var(--bg); font-family: 'DM Sans', sans-serif; color: var(--text-secondary); min-height: 100vh; }}
        h1, h2 {{ font-family: 'Cormorant Garamond', serif; color: var(--text-primary); font-weight: 400; }}
        h3 {{ font-family: 'DM Sans', sans-serif; color: var(--text-primary); font-weight: 600; font-size: 14px; }}

        /* Nav */
        .nav {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; align-items: center; gap: 32px; }}
        .nav-brand {{ font-family: 'Cormorant Garamond', serif; font-size: 24px; color: var(--text-primary); text-decoration: none; }}
        .nav-link {{ font-size: 14px; color: var(--text-muted); text-decoration: none; font-weight: 500; padding: 6px 12px; border-radius: 6px; }}
        .nav-link:hover {{ color: var(--text-primary); background: var(--surface-raised); }}
        .nav-link.active {{ color: var(--accent); background: var(--accent-subtle); }}
        .nav-right {{ margin-left: auto; }}

        /* Layout */
        .container {{ max-width: 1100px; margin: 0 auto; padding: 32px 24px; }}
        .gold-divider {{ height: 1px; background: linear-gradient(90deg, transparent, var(--gold), transparent); margin: 24px 0; }}

        /* Cards */
        .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 16px; }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}

        /* Metrics */
        .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .metric {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
        .metric-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); font-weight: 600; }}
        .metric-value {{ font-family: 'Geist Mono', monospace; font-size: 28px; color: var(--text-primary); margin-top: 4px; }}

        /* Product cards */
        .product {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 12px; display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: start; }}
        .product:hover {{ border-color: var(--accent); }}
        .product-name {{ font-family: 'Cormorant Garamond', serif; font-size: 20px; color: var(--text-primary); }}
        .product-detail {{ font-size: 13px; color: var(--text-muted); margin-top: 4px; }}
        .product-price {{ font-family: 'Geist Mono', monospace; font-size: 20px; color: var(--text-primary); text-align: right; }}
        .product-margin {{ font-size: 12px; color: var(--success); }}
        .product-tags {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }}
        .tag {{ background: var(--surface-raised); border: 1px solid var(--border-light); border-radius: 9999px; padding: 2px 10px; font-size: 11px; color: var(--text-muted); }}

        /* Forms */
        .form-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
        .form-group {{ margin-bottom: 16px; }}
        .form-group label {{ display: block; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 6px; }}
        .form-group input, .form-group select, .form-group textarea {{
            width: 100%; padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px;
            font-family: 'DM Sans', sans-serif; font-size: 14px; color: var(--text-primary);
            background: var(--surface); transition: border-color 150ms;
        }}
        .form-group input:focus, .form-group select:focus, .form-group textarea:focus {{
            outline: none; border-color: var(--accent);
        }}
        .form-group textarea {{ resize: vertical; min-height: 80px; }}
        .form-full {{ grid-column: 1 / -1; }}

        /* Buttons */
        .btn {{ padding: 10px 24px; border-radius: 8px; font-family: 'DM Sans', sans-serif; font-weight: 500; font-size: 14px; cursor: pointer; border: 1px solid var(--border); background: var(--surface); color: var(--text-primary); transition: all 150ms; }}
        .btn:hover {{ border-color: var(--accent); color: var(--accent); }}
        .btn-primary {{ background: var(--accent); border-color: var(--accent); color: var(--text-primary); }}
        .btn-primary:hover {{ background: var(--accent-hover); border-color: var(--accent-hover); }}
        .btn-sm {{ padding: 6px 14px; font-size: 13px; }}
        .btn-danger {{ color: var(--error); border-color: var(--error); }}
        .btn-danger:hover {{ background: var(--error-bg); }}

        /* Alerts */
        .alert {{ padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }}
        .alert-success {{ background: var(--success-bg); color: var(--success); }}
        .alert-error {{ background: var(--error-bg); color: var(--error); }}

        /* Login */
        .login-box {{ max-width: 360px; margin: 120px auto; }}
        .login-box h1 {{ text-align: center; font-size: 32px; margin-bottom: 4px; }}
        .login-box p {{ text-align: center; font-size: 14px; color: var(--text-muted); margin-bottom: 24px; }}

        /* Delete form inline */
        .delete-form {{ display: inline; }}
    </style>
</head>
<body>
    <nav class="nav">
        <a href="/dashboard" class="nav-brand">Pinaka</a>
        <a href="/dashboard" class="nav-link {"active" if active == "products" else ""}">Products</a>
        <a href="/dashboard/add" class="nav-link {"active" if active == "add" else ""}">+ Add Product</a>
        <a href="/dashboard/images" class="nav-link {"active" if active == "images" else ""}">Shopify Images</a>
        <a href="/dashboard/ad-creatives" class="nav-link {"active" if active == "ad-creatives" else ""}">Ad Creatives</a>
        <a href="/dashboard/pipeline" class="nav-link {"active" if active == "pipeline" else ""}">Pipeline</a>
        <div class="nav-right">
            <a href="/dashboard/logout" class="nav-link">Logout</a>
        </div>
    </nav>
    <div class="container">
        {body}
    </div>
</body>
</html>"""


# ── Login ──

@router.get("/login", response_class=HTMLResponse)
async def login_page(msg: str = ""):
    alert = f'<div class="alert alert-error">{msg}</div>' if msg else ""
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Login — Pinaka</title>
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500&family=DM+Sans:wght@400;500&display=swap" rel="stylesheet">
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:#FAF7F2; font-family:'DM Sans',sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; }}
        .box {{ width:360px; background:#fff; border:1px solid #E8E2D9; border-radius:12px; padding:40px 32px; }}
        h1 {{ font-family:'Cormorant Garamond',serif; font-weight:400; font-size:28px; text-align:center; color:#2C2825; }}
        p {{ text-align:center; font-size:14px; color:#9E9893; margin:4px 0 24px; }}
        input {{ width:100%; padding:10px 14px; border:1px solid #E8E2D9; border-radius:8px; font-size:14px; font-family:'DM Sans',sans-serif; margin-bottom:16px; }}
        input:focus {{ outline:none; border-color:#D4A017; }}
        button {{ width:100%; padding:10px; background:#D4A017; color:#2C2825; border:none; border-radius:8px; font-family:'DM Sans',sans-serif; font-weight:500; font-size:14px; cursor:pointer; }}
        button:hover {{ background:#B8890F; }}
        .alert {{ padding:10px; border-radius:8px; background:rgba(196,57,45,0.08); color:#C4392D; font-size:13px; margin-bottom:16px; }}
        .divider {{ height:1px; background:linear-gradient(90deg,transparent,#C5A55A,transparent); margin:0 0 24px; }}
    </style>
</head><body>
    <div class="box">
        <h1>Pinaka</h1>
        <p>Dashboard Login</p>
        <div class="divider"></div>
        {alert}
        <form method="post" action="/dashboard/login">
            <input type="password" name="password" placeholder="Password" autofocus>
            <button type="submit">Enter</button>
        </form>
    </div>
</body></html>"""
    return HTMLResponse(html)


@router.post("/login")
async def login_submit(password: str = Form("")):
    if not settings.dashboard_password or hmac.compare_digest(password, settings.dashboard_password):
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("dash_token", settings.dashboard_password or "dev", httponly=True, max_age=86400 * 7)
        return response
    return RedirectResponse("/dashboard/login?msg=Incorrect+password", status_code=303)


@router.get("/logout")
async def logout():
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie("dash_token")
    return response


# ── Products List ──

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def products_page(dash_token: str | None = Cookie(None), msg: str = ""):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    # Pull products from Shopify (source of truth)
    shopify_products = []
    shopify_error = ""
    if settings.shopify_shop_domain and settings.shopify_access_token:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    _shopify_api("products.json?limit=100&fields=id,title,status,variants,images,product_type,tags,created_at"),
                    headers=_shopify_headers(),
                )
                resp.raise_for_status()
                shopify_products = resp.json().get("products", [])
        except Exception as e:
            shopify_error = str(e)

    # Also load local catalog (for embedded search data)
    local_products = _get_db().get_all_products()

    # Metrics
    shopify_total = len(shopify_products)
    local_total = len(local_products)
    active = sum(1 for p in shopify_products if p.get("status") == "active")
    with_images = sum(1 for p in shopify_products if p.get("images"))

    try:
        from src.product.embeddings import ProductEmbeddings
        embedded = ProductEmbeddings().product_count()
    except Exception:
        embedded = 0

    alert = ""
    if msg:
        alert = f'<div class="alert alert-success">{msg}</div>'
    if shopify_error:
        alert += f'<div class="alert alert-error">Shopify sync error: {shopify_error}</div>'

    # Shopify product cards
    product_cards = ""
    for p in shopify_products:
        pid = p["id"]
        title = p["title"]
        status = p.get("status", "unknown")
        product_type = p.get("product_type", "")
        tags = p.get("tags", "")
        images = p.get("images", [])
        variants = p.get("variants", [])
        img_count = len(images)
        sku = variants[0].get("sku", "") if variants else ""

        # First image thumbnail
        thumb = ""
        if images:
            thumb = f'<img src="{images[0]["src"]}" style="width:80px;height:80px;object-fit:cover;border-radius:8px;border:1px solid var(--border);">'
        else:
            thumb = '<div style="width:80px;height:80px;border-radius:8px;background:var(--surface-raised);display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text-muted);">No image</div>'

        # Price from first variant
        price = ""
        if variants:
            price_val = variants[0].get("price", "0")
            price = f'<div style="font-family:\'Geist Mono\',monospace;font-size:18px;font-weight:500;color:var(--text-primary);">${float(price_val):,.2f}</div>'

        # Status badge
        status_color = "var(--success)" if status == "active" else "var(--text-muted)"
        status_bg = "var(--success-bg)" if status == "active" else "var(--surface-raised)"
        status_badge = f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:600;background:{status_bg};color:{status_color};text-transform:uppercase;">{status}</span>'

        # Variant count
        variant_count = len(variants)
        variant_text = f'{variant_count} variant{"s" if variant_count != 1 else ""}' if variant_count > 1 else ""

        # Tags
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in tag_list[:5])

        # Images indicator
        img_indicator = f'<span style="font-size:12px;color:{"var(--success)" if img_count > 0 else "var(--error)"};">{img_count} image{"s" if img_count != 1 else ""}</span>'

        product_cards += f"""
        <div class="product" style="display:flex;gap:16px;align-items:start;">
            <div>{thumb}</div>
            <div style="flex:1;">
                <div style="display:flex;justify-content:space-between;align-items:start;">
                    <div>
                        <div class="product-name">{title}</div>
                        <div class="product-detail">
                            {status_badge}
                            {f'&nbsp;·&nbsp; {product_type}' if product_type else ''}
                            {f'&nbsp;·&nbsp; {variant_text}' if variant_text else ''}
                            &nbsp;·&nbsp; {img_indicator}
                        </div>
                    </div>
                    {price}
                </div>
                {f'<div class="product-tags" style="margin-top:6px;">{tags_html}</div>' if tags_html else ''}
                <div style="margin-top:8px;display:flex;align-items:center;gap:8px;font-family:\'Geist Mono\',monospace;font-size:11px;color:var(--text-muted);">
                    <span>ID: {pid}</span>
                    <a href="/dashboard/edit-shopify/{pid}" style="background:var(--accent);color:#fff;padding:2px 10px;border-radius:4px;text-decoration:none;font-weight:600;">Edit</a>
                    <a href="https://{settings.shopify_shop_domain}/admin/products/{pid}" target="_blank" style="color:var(--accent);text-decoration:none;">Edit in Shopify</a>
                    <a href="/dashboard/images" style="color:var(--accent);text-decoration:none;">Images</a>
                    <form method="POST" action="/dashboard/delete-shopify/{pid}" style="display:inline;" onsubmit="return confirm('Delete {title} from Shopify? This cannot be undone.');">
                        <button type="submit" style="background:var(--error-bg);color:var(--error);border:none;padding:2px 10px;border-radius:4px;font-size:11px;font-family:\'Geist Mono\',monospace;cursor:pointer;">Delete</button>
                    </form>
                </div>
            </div>
        </div>"""

    if not shopify_products:
        if shopify_error:
            product_cards = f'<div class="card"><p style="text-align:center;padding:24px;">Could not load from Shopify. Check API credentials.</p></div>'
        else:
            product_cards = '<div class="card"><p style="text-align:center;padding:24px;">No products in Shopify yet. <a href="https://' + settings.shopify_shop_domain + '/admin/products/new" target="_blank" style="color:var(--accent);">Add your first product in Shopify Admin</a></p></div>'

    body = f"""
        {alert}
        <h1>Products</h1>
        <div class="gold-divider"></div>
        <div class="metrics">
            <div class="metric"><div class="metric-label">Shopify Products</div><div class="metric-value">{shopify_total}</div></div>
            <div class="metric"><div class="metric-label">Active</div><div class="metric-value" style="color:var(--success);">{active}</div></div>
            <div class="metric"><div class="metric-label">With Images</div><div class="metric-value">{with_images}</div></div>
            <div class="metric"><div class="metric-label">Embedded for Search</div><div class="metric-value">{embedded}</div></div>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <h3>Your Products (from Shopify)</h3>
            <div style="display:flex;gap:8px;">
                <a href="https://{settings.shopify_shop_domain}/admin/products/new" target="_blank" class="btn btn-primary">+ Add in Shopify</a>
                <a href="/dashboard/add" class="btn btn-sm" style="font-size:12px;">+ Add to Local Catalog</a>
            </div>
        </div>
        {product_cards}
        {f'<div style="margin-top:24px;padding:16px;background:var(--surface-raised);border-radius:8px;font-size:13px;color:var(--text-muted);">Local catalog: {local_total} products (for AI search/embeddings). <a href="/dashboard/add" style="color:var(--accent);">Add product details</a> for AI-powered customer service.</div>' if local_total > 0 or shopify_total > 0 else ''}
    """
    return HTMLResponse(_base_html("Products", body, active="products"))


# ── Add Product ──

def _product_form(action: str, product: dict | None = None, button_text: str = "Save Product") -> str:
    """Generate product form HTML. Reused for add and edit."""
    p = product or {}
    materials = p.get("materials", {})
    pricing = p.get("pricing", {})
    first_variant_name = next(iter(pricing.keys()), "default-7inch")
    first_variant = next(iter(pricing.values()), {})
    cert = p.get("certification") or {}

    metals_list = ["14K Yellow Gold", "14K White Gold", "14K Rose Gold", "18K Yellow Gold", "18K White Gold", "18K Rose Gold", "Platinum", "Sterling Silver"]
    metal_options = "".join(f'<option value="{m}" {"selected" if m == materials.get("metal", "14K Yellow Gold") else ""}>{m}</option>' for m in metals_list)

    # Variant options for Metal and Wrist Size
    variant_metals_available = ["Yellow Gold", "White Gold", "Rose Gold"]
    variant_sizes_available = ['6"', '6.5"', '7"', '7.5"']
    saved_variant_opts = p.get("variant_options", {})
    saved_metals = saved_variant_opts.get("metals", variant_metals_available)
    # Normalize saved sizes to always include " quote
    raw_saved_sizes = saved_variant_opts.get("sizes", variant_sizes_available)
    saved_sizes = [s if s.endswith('"') else s + '"' for s in raw_saved_sizes]
    # Per-size pricing: {size: retail_price}
    # Normalize keys — may come with or without " quote from various sources
    raw_size_pricing = saved_variant_opts.get("size_pricing", {})
    size_pricing = {}
    for k, v in raw_size_pricing.items():
        normalized = k if k.endswith('"') else k + '"'
        size_pricing[normalized] = v

    categories = ["Bracelets", "Necklaces", "Rings", "Earrings", "Pendants", "Other"]
    cat_options = "".join(f'<option value="{c}" {"selected" if c == p.get("category", "Bracelets") else ""}>{c}</option>' for c in categories)

    labs = ["None", "IGI", "GIA"]
    lab_options = "".join(f'<option value="{l}" {"selected" if l == cert.get("grading_lab", "None") else ""}>{l}</option>' for l in labs)

    return f"""
    <form method="post" action="{action}">
        <div class="card">
            <h3>Basic Info</h3>
            <div class="form-grid" style="margin-top:12px;">
                <div class="form-group">
                    <label>Product Name *</label>
                    <input type="text" name="name" value="{p.get("name", "")}" placeholder="Diamond Tennis Bracelet - Natural" required>
                </div>
                <div class="form-group">
                    <label>SKU *</label>
                    <input type="text" name="sku" value="{p.get("sku", "")}" placeholder="DTB-NAT-7-14KYG" required {"readonly" if product else ""}>
                </div>
                <div class="form-group">
                    <label>Category *</label>
                    <select name="category">{cat_options}</select>
                </div>
                <div class="form-group">
                    <label>Metal *</label>
                    <select name="metal">{metal_options}</select>
                </div>
                <div class="form-group">
                    <label>Total Carat Weight *</label>
                    <input type="number" name="total_carat" value="{materials.get("total_carat", 3.0)}" step="0.1" min="0.1" required>
                </div>
                <div class="form-group">
                    <label>Diamond Type (comma-separated) *</label>
                    <input type="text" name="diamond_type" value="{", ".join(materials.get("diamond_type", ["lab-grown", "VS1-VS2", "F-G color", "round brilliant"]))}" required>
                </div>
                <div class="form-group">
                    <label>Weight (grams)</label>
                    <input type="number" name="weight_grams" value="{materials.get("weight_grams", 12.5)}" step="0.01" min="0.01">
                </div>
            </div>
        </div>

        <div class="card">
            <h3>Pricing &amp; Variants</h3>
            <div style="margin-top:12px;">
                <div class="form-group" style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                    <div>
                        <label>Base Cost ($, private) *</label>
                        <input type="number" name="cost" value="{first_variant.get("cost", 450)}" step="1" min="0" required>
                    </div>
                    <div>
                        <label>Variant Name (internal)</label>
                        <input type="text" name="variant_name" value="{first_variant_name}">
                    </div>
                </div>
                <fieldset style="border:1px solid var(--border);border-radius:8px;padding:16px;margin:16px 0;">
                    <legend style="font-weight:600;font-size:13px;padding:0 8px;">Metal Options</legend>
                    <div style="display:flex;gap:16px;flex-wrap:wrap;">
                        {"".join(f'<label style="display:flex;align-items:center;gap:6px;font-size:14px;cursor:pointer;"><input type="checkbox" name="variant_metals" value="{m}" {"checked" if m in saved_metals else ""}> {m}</label>' for m in variant_metals_available)}
                    </div>
                </fieldset>
                <fieldset style="border:1px solid var(--border);border-radius:8px;padding:16px;margin:16px 0;">
                    <legend style="font-weight:600;font-size:13px;padding:0 8px;">Wrist Size Options &amp; Pricing</legend>
                    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;">
                        {"".join(f'''<div style="border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center;">
                            <label style="display:flex;align-items:center;justify-content:center;gap:6px;font-size:14px;font-weight:600;cursor:pointer;margin-bottom:8px;">
                                <input type="checkbox" name="variant_sizes" value='{s}' {"checked" if s in saved_sizes else ""}> {s}
                            </label>
                            <input type="number" name="price_{s.replace(chr(34),'').replace('.','_')}" value="{int(size_pricing.get(s, first_variant.get('retail', 0)))}" step="1" min="0" style="width:100%;text-align:center;font-size:14px;padding:6px;" placeholder="Retail $">
                        </div>''' for s in variant_sizes_available)}
                    </div>
                    <p style="font-size:11px;color:var(--text-muted);margin-top:8px;">Set retail price per wrist size. All metals share the same size-based price.</p>
                </fieldset>
            </div>
        </div>

        <div class="card">
            <h3>Story &amp; Details</h3>
            <div style="margin-top:12px;">
                <div class="form-group">
                    <label>Product Story *</label>
                    <textarea name="story" rows="4" placeholder="Every diamond in this bracelet was individually selected..." required>{p.get("story", "")}</textarea>
                </div>
                <div class="form-group">
                    <label>Care Instructions</label>
                    <textarea name="care" rows="3">{p.get("care_instructions", "Clean gently with warm soapy water and a soft brush. Store in the provided jewelry box when not worn. Bring it to us anytime for complimentary professional cleaning — that's our Free Lifetime Care promise.")}</textarea>
                </div>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Occasions (comma-separated)</label>
                        <input type="text" name="occasions" value="{", ".join(p.get("occasions", ["anniversary", "birthday", "graduation", "promotion", "self-purchase"]))}">
                    </div>
                    <div class="form-group">
                        <label>Tags (comma-separated)</label>
                        <input type="text" name="tags" value="{", ".join(p.get("tags", []))}">
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <h3>Certification (optional)</h3>
            <div class="form-grid" style="margin-top:12px;">
                <div class="form-group">
                    <label>Grading Lab</label>
                    <select name="cert_lab">{lab_options}</select>
                </div>
                <div class="form-group">
                    <label>Certificate Number</label>
                    <input type="text" name="cert_number" value="{cert.get("certificate_number", "")}" placeholder="LG-2026-0001">
                </div>
                <div class="form-group">
                    <label>Certified Carat</label>
                    <input type="number" name="cert_carat" value="{cert.get("carat_weight_certified", 0)}" step="0.01" min="0">
                </div>
                <div class="form-group">
                    <label>Clarity</label>
                    <input type="text" name="cert_clarity" value="{cert.get("clarity", "")}" placeholder="VS1">
                </div>
                <div class="form-group">
                    <label>Color</label>
                    <input type="text" name="cert_color" value="{cert.get("color", "")}" placeholder="F">
                </div>
            </div>
        </div>

        <div class="card">
            <h3>Options</h3>
            <div style="margin-top:12px; display:flex; gap:24px; flex-wrap:wrap; align-items:center;">
                <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer;">
                    <input type="checkbox" name="embed" value="1" checked> Embed for customer service search
                </label>
                <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer;">
                    <input type="checkbox" name="push_shopify" value="1" checked> Push to Shopify
                </label>
                <label style="display:flex; align-items:center; gap:6px; font-size:14px;">
                    Status:
                    <select name="product_status" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:14px;">
                        <option value="active" {"selected" if p.get("status","") == "active" else ""}>Active</option>
                        <option value="draft" {"selected" if p.get("status","") != "active" else ""}>Draft</option>
                    </select>
                </label>
            </div>
        </div>

        <div style="display:flex; gap:12px; margin-top:16px;">
            <button type="submit" class="btn btn-primary">{button_text}</button>
            <a href="/dashboard" class="btn">Cancel</a>
        </div>
    </form>"""


@router.get("/add", response_class=HTMLResponse)
async def add_product_page(dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    body = f"""
        <h1>Add Product</h1>
        <div class="gold-divider"></div>
        {_product_form("/dashboard/add")}
    """
    return HTMLResponse(_base_html("Add Product", body, active="add"))


def _build_shopify_variants(sku: str, variant_metals: list[str], variant_sizes: list[str], size_prices: dict[str, float]) -> list[dict]:
    """Build Shopify variant payload from Metal × Wrist Size matrix."""
    import itertools
    metal_codes = {"Yellow Gold": "YG", "White Gold": "WG", "Rose Gold": "RG"}
    variants = []
    for metal, size in itertools.product(variant_metals, variant_sizes):
        mc = metal_codes.get(metal, "XX")
        sc = size.replace('"', '').replace('.', '')
        variant_sku = f"{sku}-{mc}-{sc}"
        price = size_prices.get(size, 0)
        variants.append({
            "option1": metal,
            "option2": size,
            "price": str(price),
            "sku": variant_sku,
            "inventory_management": None,
            "inventory_policy": "continue",
            "requires_shipping": True,
        })
    return variants


@router.post("/add")
async def add_product_submit(
    request: Request,
    dash_token: str | None = Cookie(None),
    name: str = Form(""),
    sku: str = Form(""),
    category: str = Form("Bracelets"),
    metal: str = Form("14K Yellow Gold"),
    total_carat: float = Form(3.0),
    diamond_type: str = Form(""),
    weight_grams: float = Form(12.5),
    variant_name: str = Form("default-7inch"),
    cost: float = Form(0),
    story: str = Form(""),
    care: str = Form(""),
    occasions: str = Form(""),
    tags: str = Form(""),
    cert_lab: str = Form("None"),
    cert_number: str = Form(""),
    cert_carat: float = Form(0),
    cert_clarity: str = Form(""),
    cert_color: str = Form(""),
    embed: str = Form(""),
    push_shopify: str = Form(""),
    product_status: str = Form("active"),
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    if not name or not sku or not story:
        return RedirectResponse("/dashboard/add", status_code=303)

    # Parse multi-value form fields
    form_data = await request.form()
    variant_metals = form_data.getlist("variant_metals")
    raw_sizes = form_data.getlist("variant_sizes")
    # Normalize sizes to always include " quote (HTML attribute may strip it)
    variant_sizes = [s if s.endswith('"') else s + '"' for s in raw_sizes]

    # Per-size pricing
    size_prices: dict[str, float] = {}
    for s in ['6"', '6.5"', '7"', '7.5"']:
        field_name = f"price_{s.replace(chr(34), '').replace('.', '_')}"
        val = form_data.get(field_name, "0")
        try:
            size_prices[s] = float(val) if val else 0
        except ValueError:
            size_prices[s] = 0

    # Use first size price as the representative retail for internal records
    retail = next((size_prices[s] for s in variant_sizes if size_prices.get(s)), 0)

    product_data = {
        "sku": sku,
        "name": name,
        "category": category,
        "materials": {
            "metal": metal,
            "weight_grams": weight_grams,
            "diamond_type": [t.strip() for t in diamond_type.split(",") if t.strip()],
            "total_carat": total_carat,
        },
        "pricing": {
            variant_name: {"cost": cost, "retail": retail},
        },
        "story": story,
        "care_instructions": care,
        "occasions": [o.strip() for o in occasions.split(",") if o.strip()],
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
    }

    if cert_lab != "None" and cert_number:
        product_data["certification"] = {
            "certificate_number": cert_number,
            "grading_lab": cert_lab,
            "carat_weight_certified": cert_carat or total_carat,
            "clarity": cert_clarity,
            "color": cert_color,
        }

    # Save to Supabase (include variant options for form re-population)
    db_record = {
        "sku": sku,
        "name": name,
        "category": category,
        "materials": product_data["materials"],
        "pricing": product_data["pricing"],
        "variant_options": {
            "metals": variant_metals,
            "sizes": variant_sizes,
            "size_pricing": size_prices,
        },
        "story": story,
        "care_instructions": care,
        "occasions": product_data["occasions"],
        "tags": product_data["tags"],
    }
    if product_data.get("certification"):
        db_record["certification"] = product_data["certification"]

    _get_db().upsert_product(db_record)

    # Embed for search
    if embed:
        try:
            from src.product.embeddings import ProductEmbeddings
            from src.product.schema import Product
            emb = ProductEmbeddings()
            product_obj = Product(**product_data)
            emb.embed_product(product_obj)
            logger.info("Product %s embedded (%d total)", sku, emb.product_count())
        except Exception:
            logger.exception("Embedding failed for %s", sku)

    # Push to Shopify as active product with Metal × Wrist Size variants
    shopify_msg = ""
    if push_shopify and settings.shopify_shop_domain and settings.shopify_access_token:
        try:
            tags_list = list(product_data.get("tags", []))
            tags_list.append(sku)

            # Build variant matrix
            if variant_metals and variant_sizes:
                shopify_variants = _build_shopify_variants(sku, variant_metals, variant_sizes, size_prices)
                options_payload = [
                    {"name": "Metal"},
                    {"name": "Wrist Size"},
                ]
            else:
                # Fallback: single variant if no options selected
                shopify_variants = [{"title": variant_name, "price": str(retail), "sku": sku, "inventory_management": None}]
                options_payload = None

            shopify_payload = {
                "product": {
                    "title": name,
                    "body_html": f"<p>{story}</p><p><strong>Care:</strong> {care}</p>",
                    "vendor": "Pinaka Jewellery",
                    "product_type": category,
                    "tags": ", ".join(tags_list),
                    "status": product_status,
                    "variants": shopify_variants,
                }
            }
            if options_payload:
                shopify_payload["product"]["options"] = options_payload

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    _shopify_api("products.json"),
                    headers=_shopify_headers(),
                    json=shopify_payload,
                )
                if resp.status_code in (200, 201):
                    shopify_id = resp.json().get("product", {}).get("id", "")
                    if shopify_id:
                        _get_db().upsert_product({"sku": sku, "name": name, "shopify_product_id": shopify_id})
                        await _upsert_google_metafields(shopify_id, sku, category, metal)
                    n_variants = len(shopify_variants)
                    shopify_msg = f"+and+created+in+Shopify+(ID:+{shopify_id},+{n_variants}+variants)"
                    logger.info("Product %s pushed to Shopify (ID: %s, %d variants)", sku, shopify_id, n_variants)
                else:
                    error_detail = resp.json().get("errors", resp.text[:200])
                    shopify_msg = f"+but+Shopify+push+failed:+{error_detail}"
                    logger.error("Shopify push failed for %s: %s", sku, resp.text[:300])
        except Exception as e:
            shopify_msg = f"+but+Shopify+push+failed:+{e}"
            logger.exception("Shopify push failed for %s", sku)

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+saved{shopify_msg}", status_code=303)


# ── Edit Product ──


@router.get("/edit-shopify/{product_id}", response_class=HTMLResponse)
async def edit_product_by_shopify_id(product_id: int, dash_token: str | None = Cookie(None)):
    """Edit a product by Shopify product ID. Loads from Shopify, saves to local catalog."""
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    product = await _load_product_from_shopify_by_id(product_id)
    if not product:
        return RedirectResponse("/dashboard?msg=Product+not+found+in+Shopify", status_code=303)

    sku = product["sku"]

    # Save to local catalog so shopify_product_id is persisted
    _get_db().upsert_product(product)

    body = f"""
        <h1>Edit Product</h1>
        <div class="gold-divider"></div>
        {_product_form(f"/dashboard/edit/{sku}", product, button_text="Update Product")}
        <div class="gold-divider"></div>
        <div class="card">
            <h3>Ad Creatives</h3>
            <p style="font-size: 14px; color: var(--text-muted); margin: 8px 0 16px 0;">
                Generate 3 Claude-drafted ad copy variants for this product.
                Drafts appear on the Ad Creatives page for review.
            </p>
            <form method="post" action="/dashboard/ad-creatives/generate" style="display:inline;">
                <input type="hidden" name="sku" value="{sku}">
                <button type="submit" class="btn btn-primary">Generate Ad Drafts</button>
            </form>
            <a href="/dashboard/ad-creatives" class="btn" style="margin-left:8px;">View All Drafts →</a>
        </div>
    """
    return HTMLResponse(_base_html("Edit Product", body, active="products"))


@router.get("/edit/{sku}", response_class=HTMLResponse)
async def edit_product_page(sku: str, dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    product = _get_db().get_product_by_sku(sku)

    # If not in local catalog, try loading from Shopify by SKU
    if not product and settings.shopify_shop_domain and settings.shopify_access_token:
        product = await _load_product_from_shopify(sku)

    if not product:
        return RedirectResponse("/dashboard?msg=Product+not+found", status_code=303)

    body = f"""
        <h1>Edit Product</h1>
        <div class="gold-divider"></div>
        {_product_form(f"/dashboard/edit/{sku}", product, button_text="Update Product")}
        <div class="gold-divider"></div>
        <div class="card">
            <h3>Ad Creatives</h3>
            <p style="font-size: 14px; color: var(--text-muted); margin: 8px 0 16px 0;">
                Generate 3 Claude-drafted ad copy variants for this product.
                Drafts appear on the Ad Creatives page for review.
            </p>
            <form method="post" action="/dashboard/ad-creatives/generate" style="display:inline;">
                <input type="hidden" name="sku" value="{sku}">
                <button type="submit" class="btn btn-primary">Generate Ad Drafts</button>
            </form>
            <a href="/dashboard/ad-creatives" class="btn" style="margin-left:8px;">View All Drafts →</a>
        </div>
    """
    return HTMLResponse(_base_html("Edit Product", body, active="products"))


@router.post("/edit/{sku}")
async def edit_product_submit(
    request: Request,
    sku: str,
    dash_token: str | None = Cookie(None),
    name: str = Form(""),
    category: str = Form("Bracelets"),
    metal: str = Form("14K Yellow Gold"),
    total_carat: float = Form(3.0),
    diamond_type: str = Form(""),
    weight_grams: float = Form(12.5),
    variant_name: str = Form("default-7inch"),
    cost: float = Form(0),
    story: str = Form(""),
    care: str = Form(""),
    occasions: str = Form(""),
    tags: str = Form(""),
    cert_lab: str = Form("None"),
    cert_number: str = Form(""),
    cert_carat: float = Form(0),
    cert_clarity: str = Form(""),
    cert_color: str = Form(""),
    embed: str = Form(""),
    push_shopify: str = Form(""),
    product_status: str = Form("draft"),
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    # Parse multi-value form fields
    form_data = await request.form()
    variant_metals = form_data.getlist("variant_metals")
    raw_sizes = form_data.getlist("variant_sizes")
    # Normalize sizes to always include " quote (HTML attribute may strip it)
    variant_sizes = [s if s.endswith('"') else s + '"' for s in raw_sizes]

    # Per-size pricing — read from price_6, price_6_5, price_7, price_7_5 fields
    size_prices: dict[str, float] = {}
    for s in ['6"', '6.5"', '7"', '7.5"']:
        field_name = f"price_{s.replace(chr(34), '').replace('.', '_')}"
        val = form_data.get(field_name, "0")
        try:
            size_prices[s] = float(val) if val else 0
        except ValueError:
            size_prices[s] = 0

    retail = next((size_prices[s] for s in variant_sizes if size_prices.get(s)), 0)

    product_data = {
        "sku": sku,
        "name": name,
        "category": category,
        "materials": {
            "metal": metal,
            "weight_grams": weight_grams,
            "diamond_type": [t.strip() for t in diamond_type.split(",") if t.strip()],
            "total_carat": total_carat,
        },
        "pricing": {
            variant_name: {"cost": cost, "retail": retail},
        },
        "story": story,
        "care_instructions": care,
        "occasions": [o.strip() for o in occasions.split(",") if o.strip()],
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
    }

    if cert_lab != "None" and cert_number:
        product_data["certification"] = {
            "certificate_number": cert_number,
            "grading_lab": cert_lab,
            "carat_weight_certified": cert_carat or total_carat,
            "clarity": cert_clarity,
            "color": cert_color,
        }

    # Save to Supabase
    db_record = {
        "sku": sku,
        "name": name,
        "category": category,
        "materials": product_data["materials"],
        "pricing": product_data["pricing"],
        "variant_options": {
            "metals": variant_metals,
            "sizes": variant_sizes,
            "size_pricing": size_prices,
        },
        "story": story,
        "care_instructions": care,
        "occasions": product_data["occasions"],
        "tags": product_data["tags"],
    }
    if product_data.get("certification"):
        db_record["certification"] = product_data["certification"]

    _get_db().upsert_product(db_record)

    if embed:
        try:
            from src.product.embeddings import ProductEmbeddings
            from src.product.schema import Product
            emb = ProductEmbeddings()
            product_obj = Product(**product_data)
            emb.embed_product(product_obj)
        except Exception:
            logger.exception("Embedding failed for %s", sku)

    # Sync edit to Shopify if product exists there
    shopify_msg = ""
    if push_shopify and settings.shopify_shop_domain and settings.shopify_access_token:
        existing = _get_db().get_product_by_sku(sku)
        shopify_id = (existing or {}).get("shopify_product_id")

        if shopify_id:
            try:
                tags_list = list(product_data.get("tags", []))
                tags_list.append(sku)

                update_payload = {
                    "product": {
                        "id": shopify_id,
                        "title": name,
                        "body_html": f"<p>{story}</p><p><strong>Care:</strong> {care}</p>",
                        "product_type": category,
                        "tags": ", ".join(tags_list),
                        "status": product_status,
                    }
                }

                # Include variant updates if options are set
                if variant_metals and variant_sizes:
                    update_payload["product"]["options"] = [
                        {"name": "Metal"},
                        {"name": "Wrist Size"},
                    ]
                    update_payload["product"]["variants"] = _build_shopify_variants(
                        sku, variant_metals, variant_sizes, size_prices
                    )

                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.put(
                        _shopify_api(f"products/{shopify_id}.json"),
                        headers=_shopify_headers(),
                        json=update_payload,
                    )
                    if resp.status_code == 200:
                        await _upsert_google_metafields(shopify_id, sku, category, metal)
                        n_variants = len(update_payload["product"].get("variants", []))
                        shopify_msg = f"+and+updated+in+Shopify+({n_variants}+variants)" if n_variants else "+and+updated+in+Shopify"
                    else:
                        shopify_msg = f"+but+Shopify+update+failed:+{resp.status_code}"
            except Exception as e:
                shopify_msg = f"+but+Shopify+sync+failed:+{e}"
                logger.exception("Shopify update failed for %s", sku)
        else:
            shopify_msg = "+(not+linked+to+Shopify)"

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+updated{shopify_msg}", status_code=303)


# ── Delete Product ──

@router.post("/delete-shopify/{product_id}")
async def delete_shopify_product(product_id: int, dash_token: str | None = Cookie(None)):
    """Delete a product directly from Shopify by product ID."""
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                _shopify_api(f"products/{product_id}.json"),
                headers=_shopify_headers(),
            )
            if resp.status_code in (200, 204):
                msg = f"Product+{product_id}+deleted+from+Shopify"
            else:
                msg = f"Shopify+delete+failed:+{resp.status_code}"
    except Exception as e:
        msg = f"Delete+failed:+{e}"

    return RedirectResponse(f"/dashboard?msg={msg}", status_code=303)


@router.post("/delete/{sku}")
async def delete_product(sku: str, dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    # Check if product is linked to Shopify
    product = _get_db().get_product_by_sku(sku)
    shopify_id = (product or {}).get("shopify_product_id")

    # Delete from local DB
    _get_db().delete_product(sku)

    # Delete from Shopify too
    shopify_msg = ""
    if shopify_id and settings.shopify_shop_domain and settings.shopify_access_token:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.delete(
                    _shopify_api(f"products/{shopify_id}.json"),
                    headers=_shopify_headers(),
                )
                if resp.status_code in (200, 204):
                    shopify_msg = "+and+removed+from+Shopify"
                else:
                    shopify_msg = f"+but+Shopify+delete+failed:+{resp.status_code}"
        except Exception as e:
            shopify_msg = f"+but+Shopify+delete+failed:+{e}"

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+deleted{shopify_msg}", status_code=303)


# ── Shopify Images ──

def _shopify_product_to_local(p: dict) -> dict:
    """Convert a Shopify product dict to local catalog format."""
    import re as _re

    variants = p.get("variants", [])
    first_sku = variants[0].get("sku", "") if variants else ""

    # Extract unique option values
    metals = sorted({v["option1"] for v in variants if v.get("option1") and v["option1"] != "Default Title"})
    sizes = sorted({v["option2"] for v in variants if v.get("option2")})

    # Normalize sizes to include " quote (e.g. '6' -> '6"')
    normalized_sizes = []
    for s in sizes:
        if not s.endswith('"'):
            s = s + '"'
        normalized_sizes.append(s)
    sizes = normalized_sizes

    size_prices = {}
    for v in variants:
        if v.get("option2"):
            size_key = v["option2"]
            if not size_key.endswith('"'):
                size_key = size_key + '"'
            size_prices[size_key] = float(v.get("price", 0))

    # Derive base product SKU by stripping variant suffix (-YG-6, -WG-65, etc.)
    # Pattern: base SKU ends before the metal code
    sku = first_sku or f"SHOP-{p['id']}"
    if first_sku and metals:
        metal_codes = {"Yellow Gold": "-YG-", "White Gold": "-WG-", "Rose Gold": "-RG-"}
        for mc in metal_codes.values():
            idx = first_sku.find(mc)
            if idx > 0:
                sku = first_sku[:idx]
                break

    body_html = p.get("body_html", "") or ""
    # Strip basic HTML tags for the story field
    story = _re.sub(r"<[^>]+>", " ", body_html).strip()
    story = _re.sub(r"\s+", " ", story)

    return {
        "sku": sku,
        "name": p.get("title", ""),
        "category": p.get("product_type", "Bracelets"),
        "shopify_product_id": p["id"],
        "materials": {"metal": metals[0] if metals else "", "total_carat": 3.0, "weight_grams": 12.5, "diamond_type": []},
        "pricing": {sku: {"cost": 0, "retail": float(variants[0].get("price", 0)) if variants else 0}},
        "variant_options": {"metals": metals, "sizes": sizes, "size_pricing": size_prices},
        "story": story,
        "care_instructions": "",
        "occasions": [],
        "tags": [t.strip() for t in p.get("tags", "").split(",") if t.strip()],
        "certification": None,
    }


async def _load_product_from_shopify_by_id(product_id: int) -> dict | None:
    """Load a product from Shopify by product ID.

    If the product already exists in the local catalog (by shopify_product_id),
    merges Shopify variant data into the existing record so SKU stays consistent.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _shopify_api(f"products/{product_id}.json"),
                headers=_shopify_headers(),
            )
            if resp.status_code != 200:
                return None
            p = resp.json().get("product")
            if not p:
                return None

            shopify_data = _shopify_product_to_local(p)

            # Check if product already exists in local catalog
            db = _get_db()
            all_products = db.get_all_products()
            for existing in all_products:
                if existing.get("shopify_product_id") == product_id:
                    # Merge: keep existing SKU and local fields, update variant info from Shopify
                    existing["variant_options"] = shopify_data["variant_options"]
                    existing.setdefault("materials", shopify_data["materials"])
                    return existing

            return shopify_data
    except Exception:
        logger.exception("Failed to load product from Shopify ID %s", product_id)
        return None


async def _load_product_from_shopify(sku: str) -> dict | None:
    """Load a product from Shopify by matching variant SKU."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _shopify_api("products.json?limit=50"),
                headers=_shopify_headers(),
            )
            if resp.status_code != 200:
                return None

            for p in resp.json().get("products", []):
                for v in p.get("variants", []):
                    if v.get("sku") == sku:
                        return _shopify_product_to_local(p)
        return None
    except Exception:
        logger.exception("Failed to load product from Shopify for SKU %s", sku)
        return None


def _shopify_headers():
    return {
        "X-Shopify-Access-Token": settings.shopify_access_token,
        "Content-Type": "application/json",
    }


def _shopify_api(path: str) -> str:
    return f"https://{settings.shopify_shop_domain}/admin/api/{settings.shopify_api_version}/{path}"


# Google product taxonomy IDs — Apparel & Accessories > Jewelry > *
_GOOGLE_CATEGORY_IDS = {
    "Bracelets": "189",
    "Earrings": "194",
    "Necklaces": "191",
    "Rings": "200",
    "Pendants": "5122",
    "Watches": "201",
}


async def _upsert_google_metafields(
    product_id: int,
    sku: str,
    category: str,
    metal: str = "",
) -> None:
    """Set Google Shopping metafields on a Shopify product so Google Merchant Center
    accepts it without a real GTIN and with full visibility in Apparel & Accessories.
    Handmade jewelry uses: custom_product=TRUE, mpn=<sku>, brand=<vendor>, condition=new,
    google_product_category=<jewelry>, age_group=adult, gender=unisex, color=<metal>.
    Safe to call on create or edit — existing metafields are updated in place.
    """
    google_cat = _GOOGLE_CATEGORY_IDS.get(category, "188")  # 188 = generic Jewelry
    color = metal or "Gold"  # fall back to generic "Gold" if metal not provided
    desired = [
        ("mm-google-shopping", "mpn", sku),
        ("mm-google-shopping", "condition", "new"),
        ("mm-google-shopping", "custom_product", "TRUE"),
        ("mm-google-shopping", "google_product_category", google_cat),
        ("mm-google-shopping", "age_group", "adult"),
        ("mm-google-shopping", "gender", "unisex"),
        ("mm-google-shopping", "color", color),
    ]

    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch existing metafields so we can PUT instead of POST on duplicates
        existing_by_key: dict[tuple[str, str], int] = {}
        try:
            resp = await client.get(
                _shopify_api(f"products/{product_id}/metafields.json"),
                headers=_shopify_headers(),
            )
            if resp.status_code == 200:
                for mf in resp.json().get("metafields", []):
                    existing_by_key[(mf["namespace"], mf["key"])] = mf["id"]
        except Exception:
            logger.exception("Failed to list metafields for product %s", product_id)

        for namespace, key, value in desired:
            payload = {
                "metafield": {
                    "namespace": namespace,
                    "key": key,
                    "value": value,
                    "type": "single_line_text_field",
                }
            }
            mf_id = existing_by_key.get((namespace, key))
            try:
                if mf_id:
                    payload["metafield"]["id"] = mf_id
                    await client.put(
                        _shopify_api(f"metafields/{mf_id}.json"),
                        headers=_shopify_headers(),
                        json=payload,
                    )
                else:
                    await client.post(
                        _shopify_api(f"products/{product_id}/metafields.json"),
                        headers=_shopify_headers(),
                        json=payload,
                    )
            except Exception:
                logger.exception("Failed to set metafield %s.%s for product %s", namespace, key, product_id)


IMAGE_ROLES = ["Hero Shot", "On Wrist", "Detail / Clasp", "Lifestyle", "Flat Lay", "Packaging", "Other"]


def _role_from_alt(alt: str) -> str:
    """Extract image role from alt text convention: 'Product Name | Role'."""
    if not alt or "|" not in alt:
        return ""
    return alt.split("|")[-1].strip()


def _role_badge(role: str) -> str:
    colors = {
        "Hero Shot": ("var(--accent-subtle)", "var(--accent)"),
        "On Wrist": ("var(--success-bg)", "var(--success)"),
        "Detail / Clasp": ("rgba(59,126,197,0.08)", "var(--info)"),
        "Lifestyle": ("rgba(193,126,26,0.08)", "var(--warning)"),
        "Flat Lay": ("var(--surface-raised)", "var(--text-secondary)"),
        "Packaging": ("var(--surface-raised)", "var(--text-secondary)"),
    }
    bg, fg = colors.get(role, ("var(--surface-raised)", "var(--text-muted)"))
    if not role:
        return ""
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:600;background:{bg};color:{fg};text-transform:uppercase;letter-spacing:0.5px;">{role}</span>'


@router.get("/images", response_class=HTMLResponse)
async def images_page(dash_token: str | None = Cookie(None), msg: str = ""):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    if not settings.shopify_shop_domain or not settings.shopify_access_token:
        body = '<div class="container"><div class="card"><p>Shopify credentials not configured.</p></div></div>'
        return HTMLResponse(_base_html("Shopify Images", body, active="images"))

    # Fetch products with variants
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _shopify_api("products.json?limit=50&fields=id,title,images,variants"),
                headers=_shopify_headers(),
            )
            resp.raise_for_status()
            products = resp.json().get("products", [])
    except Exception as e:
        body = f'<div class="container"><div class="card"><p>Failed to load products: {e}</p></div></div>'
        return HTMLResponse(_base_html("Shopify Images", body, active="images"))

    # Message banner
    msg_html = ""
    if msg:
        is_err = "error" in msg.lower() or "fail" in msg.lower()
        bg = "var(--error-bg)" if is_err else "var(--success-bg)"
        fg = "var(--error)" if is_err else "var(--success)"
        msg_html = f'<div style="background:{bg};color:{fg};padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:14px;">{msg}</div>'

    # Summary metrics
    total_products = len(products)
    total_images = sum(len(p.get("images", [])) for p in products)
    no_images = sum(1 for p in products if not p.get("images"))

    summary = f"""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px;">
        <div class="card" style="margin:0;padding:16px;">
            <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;">Products</div>
            <div style="font-family:'Geist Mono',monospace;font-size:24px;color:var(--text-primary);">{total_products}</div>
        </div>
        <div class="card" style="margin:0;padding:16px;">
            <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;">Total Images</div>
            <div style="font-family:'Geist Mono',monospace;font-size:24px;color:var(--text-primary);">{total_images}</div>
        </div>
        <div class="card" style="margin:0;padding:16px;">
            <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;">Missing Images</div>
            <div style="font-family:'Geist Mono',monospace;font-size:24px;color:{"var(--error)" if no_images else "var(--success)"};">{no_images}</div>
        </div>
    </div>"""

    # Product cards
    products_html = ""
    for p in products:
        pid = p["id"]
        title = p["title"]
        images = sorted(p.get("images", []), key=lambda i: i.get("position", 0))
        variants = p.get("variants", [])
        img_count = len(images)

        # Variant info
        variant_names = [v["title"] for v in variants if v["title"] != "Default Title"]
        variant_info = f' <span style="font-size:12px;color:var(--text-muted);">({", ".join(variant_names)})</span>' if variant_names else ""

        # Status indicator
        if img_count == 0:
            status = '<span style="color:var(--error);font-size:12px;font-weight:600;">No images</span>'
        elif img_count < 3:
            status = f'<span style="color:var(--warning);font-size:12px;">{img_count} image{"s" if img_count != 1 else ""} (add more)</span>'
        else:
            status = f'<span style="color:var(--success);font-size:12px;">{img_count} images</span>'

        # Image grid with position numbers, roles, and variant links
        images_grid = ""
        if images:
            img_cards = ""
            for img in images:
                pos = img.get("position", 0)
                alt = img.get("alt", "") or ""
                role = _role_from_alt(alt)
                badge = _role_badge(role)
                pos_label = "PRIMARY" if pos == 1 else f"#{pos}"
                pos_color = "var(--accent)" if pos == 1 else "var(--text-muted)"

                # Variant link indicator
                linked_variants = img.get("variant_ids", [])
                variant_tag = ""
                if linked_variants:
                    vnames = [v["title"] for v in variants if v["id"] in linked_variants]
                    if vnames:
                        variant_tag = f'<div style="font-size:10px;color:var(--info);margin-top:2px;">Variant: {", ".join(vnames)}</div>'

                img_cards += f"""
                <div style="text-align:center;width:160px;">
                    <div style="position:relative;">
                        <img src="{img['src']}" alt="{alt}" style="width:160px;height:160px;object-fit:cover;border-radius:8px;border:1px solid var(--border);">
                        <span style="position:absolute;top:6px;left:6px;background:var(--surface);padding:1px 6px;border-radius:4px;font-family:'Geist Mono',monospace;font-size:9px;font-weight:600;color:{pos_color};border:1px solid var(--border);">{pos_label}</span>
                    </div>
                    <div style="margin-top:6px;">{badge}</div>
                    {variant_tag}
                    <div style="margin-top:4px;display:flex;gap:4px;justify-content:center;">
                        <form method="POST" action="/dashboard/images/set-primary/{pid}/{img['id']}" style="display:inline;">
                            <button type="submit" {"disabled" if pos == 1 else ""} style="background:{"var(--surface-raised)" if pos == 1 else "var(--accent-subtle)"};color:{"var(--text-muted)" if pos == 1 else "var(--accent)"};border:none;padding:3px 8px;border-radius:4px;font-size:10px;cursor:pointer;">Primary</button>
                        </form>
                        <form method="POST" action="/dashboard/images/delete/{pid}/{img['id']}" style="display:inline;">
                            <button type="submit" style="background:var(--error-bg);color:var(--error);border:none;padding:3px 8px;border-radius:4px;font-size:10px;cursor:pointer;">Delete</button>
                        </form>
                    </div>
                </div>"""
            images_grid = f'<div style="display:flex;gap:16px;flex-wrap:wrap;margin:12px 0;">{img_cards}</div>'
        else:
            images_grid = '<p style="color:var(--text-muted);font-size:14px;padding:16px 0;">No images yet. Upload product photos below.</p>'

        # Role selector options
        role_options = "".join(f'<option value="{r}">{r}</option>' for r in IMAGE_ROLES)

        products_html += f"""
        <div class="card">
            <div class="card-header">
                <div>
                    <h2 style="font-size:20px;margin-bottom:2px;">{title}{variant_info}</h2>
                    <div style="font-family:'Geist Mono',monospace;font-size:11px;color:var(--text-muted);">ID: {pid}</div>
                </div>
                {status}
            </div>
            {images_grid}
            <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border-light);">
                <form method="POST" action="/dashboard/images/upload/{pid}" enctype="multipart/form-data">
                    <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;">
                        <div>
                            <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Upload Images</label>
                            <input type="file" name="files" accept="image/png,image/jpeg,image/webp" multiple required
                                   style="font-size:13px;padding:8px;border:1px solid var(--border);border-radius:8px;background:var(--surface);">
                        </div>
                        <div>
                            <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Image Role</label>
                            <select name="role" style="font-size:13px;padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);min-width:140px;">
                                {role_options}
                            </select>
                        </div>
                        <div>
                            <label style="font-size:12px;font-weight:600;color:var(--text-muted);display:block;margin-bottom:4px;">Alt Text (optional)</label>
                            <input type="text" name="alt_text" placeholder="Auto-generated if empty"
                                   style="font-size:13px;padding:8px 12px;border:1px solid var(--border);border-radius:8px;width:240px;">
                        </div>
                        <button type="submit" style="background:var(--accent);color:var(--text-primary);border:none;padding:10px 24px;border-radius:8px;font-weight:600;font-size:14px;cursor:pointer;">
                            Upload
                        </button>
                    </div>
                </form>
            </div>
        </div>"""

    body = f"""
    <div class="container">
        <h1>Shopify Images</h1>
        <div class="gold-divider"></div>
        <p style="margin-bottom:24px;">Manage product images. Each product shows its linked images with position, role, and variant info.</p>
        {msg_html}
        {summary}
        {products_html if products_html else '<div class="card"><p>No products in Shopify yet.</p></div>'}
    </div>"""

    return HTMLResponse(_base_html("Shopify Images", body, active="images"))


@router.post("/images/upload/{product_id}")
async def upload_images(
    product_id: int,
    files: list[UploadFile] = File(...),
    role: str = Form("Other"),
    alt_text: str = Form(""),
    dash_token: str | None = Cookie(None),
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    # Get product title for auto alt text
    product_title = "Product"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _shopify_api(f"products/{product_id}.json?fields=title"),
                headers=_shopify_headers(),
            )
            if resp.status_code == 200:
                product_title = resp.json().get("product", {}).get("title", "Product")
    except Exception:
        pass

    uploaded = 0
    errors = []

    async with httpx.AsyncClient(timeout=30) as client:
        for f in files:
            try:
                content = await f.read()
                b64 = base64.b64encode(content).decode("utf-8")
                # Alt text convention: "Product Name | Role"
                final_alt = alt_text if alt_text else f"{product_title} | {role}"
                payload = {
                    "image": {
                        "attachment": b64,
                        "filename": f.filename,
                        "alt": final_alt,
                    }
                }
                resp = await client.post(
                    _shopify_api(f"products/{product_id}/images.json"),
                    headers=_shopify_headers(),
                    json=payload,
                )
                if resp.status_code == 200:
                    uploaded += 1
                else:
                    errors.append(f"{f.filename}: {resp.text[:100]}")
            except Exception as e:
                errors.append(f"{f.filename}: {e}")

    if errors:
        msg = f"Uploaded+{uploaded}+images.+Errors:+{',+'.join(errors[:2])}"
    else:
        msg = f"Uploaded+{uploaded}+{role}+images+to+{product_title}"

    return RedirectResponse(f"/dashboard/images?msg={msg}", status_code=303)


@router.post("/images/set-primary/{product_id}/{image_id}")
async def set_primary_image(
    product_id: int,
    image_id: int,
    dash_token: str | None = Cookie(None),
):
    """Move an image to position 1 (primary/hero image shown in collection grid)."""
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                _shopify_api(f"products/{product_id}/images/{image_id}.json"),
                headers=_shopify_headers(),
                json={"image": {"id": image_id, "position": 1}},
            )
            if resp.status_code == 200:
                msg = "Image+set+as+primary"
            else:
                msg = f"Error:+{resp.status_code}"
    except Exception as e:
        msg = f"Error:+{e}"

    return RedirectResponse(f"/dashboard/images?msg={msg}", status_code=303)


@router.post("/images/delete/{product_id}/{image_id}")
async def delete_image(
    product_id: int,
    image_id: int,
    dash_token: str | None = Cookie(None),
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                _shopify_api(f"products/{product_id}/images/{image_id}.json"),
                headers=_shopify_headers(),
            )
            if resp.status_code in (200, 204):
                msg = "Image+deleted"
            else:
                msg = f"Error+deleting+image:+{resp.status_code}"
    except Exception as e:
        msg = f"Error:+{e}"

    return RedirectResponse(f"/dashboard/images?msg={msg}", status_code=303)


# ── Ad Creatives (Phase 6.1) ──
#
# Dashboard-driven creative generation and Meta push. Click "Generate Ad Drafts"
# on a product's edit page → Claude generates 3 variants in the background → drafts
# appear on /dashboard/ad-creatives for review → founder clicks Approve → Meta push
# with status=PAUSED (soft-pause window) → founder clicks Go Live for ACTIVE.


def _ad_creatives_banner_html(msg: str = "") -> str:
    """Warning banner when Meta creative push isn't fully configured."""
    from src.core.settings import settings as _settings
    if _settings.is_meta_creative_ready and not msg:
        return ""
    if msg:
        # URL-decoded success/error message from a previous action
        alert_class = "alert-error" if "Error" in msg or "Failed" in msg else "alert-success"
        return f'<div class="alert {alert_class}">{msg.replace("+", " ")}</div>'
    missing = []
    if not _settings.meta_ads_access_token:
        missing.append("META_ADS_ACCESS_TOKEN")
    if not _settings.meta_ad_account_id:
        missing.append("META_AD_ACCOUNT_ID")
    if not _settings.meta_facebook_page_id:
        missing.append("META_FACEBOOK_PAGE_ID")
    return f"""
    <div class="alert alert-error" style="margin-bottom:16px;">
        <strong>Meta push disabled</strong> — drafts can be generated and reviewed,
        but Approve buttons are inactive until these Railway env vars are set:
        <code>{', '.join(missing)}</code>.
        Create a Facebook Page for Pinaka Jewellery and link it to the Business Portfolio,
        then set <code>META_FACEBOOK_PAGE_ID</code> on Railway.
    </div>
    """


def _status_badge_html(status: str, meta_creative_id: str | None = None) -> str:
    """Render a status badge pill with aria-label for screen readers."""
    colors = {
        "pending_review": ("#C17E1A", "rgba(193,126,26,0.12)", "Pending Review"),
        "publishing":     ("#3B7EC5", "rgba(59,126,197,0.12)", "Publishing..."),
        "published":      ("#C17E1A", "rgba(193,126,26,0.12)", "Paused on Meta"),
        "live":           ("#2E7D4F", "rgba(46,125,79,0.12)", "Live on Meta"),
        "rejected":       ("#9E9893", "rgba(158,152,147,0.15)", "Rejected"),
        "paused":         ("#C4392D", "rgba(196,57,45,0.10)", "Removed"),
    }
    color, bg, label = colors.get(status, ("#6B6560", "rgba(107,101,96,0.1)", status))
    aria = f'aria-label="Status: {label}"'
    show_id = meta_creative_id and status in ("published", "live")
    extra = f" #{meta_creative_id}" if show_id else ""
    return (
        f'<span {aria} style="display:inline-block;font-size:11px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:1px;padding:4px 10px;border-radius:9999px;'
        f'color:{color};background:{bg};">{label}{extra}</span>'
    )


def _variant_card_html(creative: dict, ready: bool) -> str:
    """Render one variant card in the grid."""
    from html import escape
    status = creative.get("status", "pending_review")
    label = creative.get("variant_label", "?")
    cta = creative.get("cta", "SHOP_NOW")
    headline = escape(creative.get("headline", ""))
    primary_text = escape(creative.get("primary_text", ""))
    description = escape(creative.get("description", ""))
    image_url = escape(creative.get("image_url", ""))
    creative_id = creative.get("id", 0)
    meta_creative_id = creative.get("meta_creative_id")
    meta_ad_id = creative.get("meta_ad_id")
    warning = creative.get("validation_warning")

    warning_html = ""
    if warning:
        warning_html = (
            f'<div style="font-size:11px;color:#C4392D;margin:8px 0;padding:6px 10px;'
            f'background:rgba(196,57,45,0.08);border-radius:4px;">'
            f'⚠ {escape(str(warning))[:200]}</div>'
        )

    rejected_style = "opacity:0.5;text-decoration:line-through;" if status == "rejected" else ""

    actions_html = ""
    if status == "pending_review":
        approve_disabled = "" if ready else 'disabled style="opacity:0.4;cursor:not-allowed;"'
        actions_html = f"""
            <form method="post" action="/dashboard/ad-creatives/{creative_id}/approve" style="display:inline;width:48%;">
                <button type="submit" class="btn btn-primary" style="width:100%;" {approve_disabled}>Approve & Push</button>
            </form>
            <form method="post" action="/dashboard/ad-creatives/{creative_id}/reject" style="display:inline;width:48%;margin-left:4%;">
                <button type="submit" class="btn btn-danger" style="width:100%;">Reject</button>
            </form>
        """
    elif status == "published":
        actions_html = f"""
            <form method="post" action="/dashboard/ad-creatives/{creative_id}/set-live" style="display:inline;width:48%;">
                <button type="submit" class="btn btn-primary" style="width:100%;">Go Live</button>
            </form>
            <form method="post" action="/dashboard/ad-creatives/{creative_id}/pause" style="display:inline;width:48%;margin-left:4%;">
                <button type="submit" class="btn" style="width:100%;">Archive</button>
            </form>
        """
    elif status == "live":
        if meta_ad_id:
            ad_link = (
                f"https://adsmanager.facebook.com/adsmanager/manage/ads/edit?"
                f"selected_ad_ids={meta_ad_id}"
            )
            live_note = (
                f'<div style="font-size:11px;color:#2E7D4F;line-height:1.4;text-align:center;padding:8px 4px;">'
                f'Ad <a href="{ad_link}" target="_blank" style="color:var(--accent);text-decoration:underline;font-family:\'Geist Mono\',monospace;">#{meta_ad_id}</a> created under default Ad Set. '
                f'Flip the Ad Set to ACTIVE in Ads Manager to start serving impressions.</div>'
            )
        else:
            live_note = (
                '<div style="font-size:11px;color:var(--text-muted);line-height:1.4;text-align:center;padding:8px 4px;">'
                'Creative active in Meta Creative Library. '
                '<a href="https://adsmanager.facebook.com/adsmanager/manage/ads" target="_blank" style="color:var(--accent);text-decoration:underline;">Open Ads Manager</a> to attach it to an Ad Set.</div>'
            )
        actions_html = f"""
            {live_note}
            <form method="post" action="/dashboard/ad-creatives/{creative_id}/pause" style="display:inline;width:100%;">
                <button type="submit" class="btn" style="width:100%;">Archive from dashboard</button>
            </form>
        """
    elif status == "publishing":
        actions_html = '<div style="font-size:12px;color:var(--text-muted);text-align:center;">Pushing to Meta...</div>'
    elif status == "rejected":
        actions_html = ""

    return f"""
    <div class="card" style="{rejected_style}padding:0;overflow:hidden;">
        <img src="{image_url}" alt="Variant {label} image" style="width:100%;aspect-ratio:1/1;object-fit:cover;display:block;border-bottom:1px solid var(--border);">
        <div style="padding:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);">Variant {label}</span>
                {_status_badge_html(status, meta_creative_id)}
            </div>
            <div style="font-size:10px;font-family:'Geist Mono',monospace;color:var(--accent);margin-bottom:8px;">CTA: {cta}</div>
            <h3 style="font-size:15px;line-height:1.3;margin-bottom:8px;color:var(--text-primary);">{headline}</h3>
            <p style="font-size:13px;color:var(--text-secondary);line-height:1.5;margin-bottom:12px;max-height:80px;overflow:hidden;">{primary_text}</p>
            {f'<p style="font-size:11px;color:var(--text-muted);margin-bottom:8px;">{description}</p>' if description else ''}
            {warning_html}
            <div style="display:flex;gap:0;margin-top:12px;">
                {actions_html}
            </div>
        </div>
    </div>
    """


def _batch_section_html(batch_id: str, creatives: list[dict], ready: bool) -> str:
    """Render a batch of 3 variants as a horizontal row."""
    from html import escape
    if not creatives:
        return ""
    sku = creatives[0].get("sku", "?")
    created_at = creatives[0].get("created_at", "")[:16].replace("T", " ")
    cards_html = "".join(_variant_card_html(c, ready) for c in creatives)
    return f"""
    <div style="margin-bottom:32px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;">
            <div>
                <h3 style="font-size:14px;">{escape(sku)}</h3>
                <div style="font-size:11px;color:var(--text-muted);font-family:'Geist Mono',monospace;">
                    Batch {escape(batch_id[:8])} · {escape(created_at)}
                </div>
            </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;">
            {cards_html}
        </div>
    </div>
    """


def _empty_state_html() -> str:
    return """
    <div class="card" style="text-align:center;padding:48px 24px;">
        <h2 style="font-size:28px;margin-bottom:12px;">No ad drafts yet</h2>
        <p style="font-size:14px;color:var(--text-muted);margin-bottom:24px;">
            Generate 3 Claude-drafted ad copy variants from any product.
        </p>
        <a href="/dashboard" class="btn btn-primary">Go to Products →</a>
    </div>
    """


@router.get("/ad-creatives", response_class=HTMLResponse)
async def ad_creatives_page(
    dash_token: str | None = Cookie(None),
    msg: str = "",
    pending: str = "",
):
    """List all ad creative drafts, grouped by batch, newest first."""
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    from src.core.settings import settings as _settings
    db = _get_db()
    creatives = db.get_recent_ad_creatives(limit=60)

    # Group by batch, preserve order (first occurrence wins = newest first)
    batches: dict[str, list[dict]] = {}
    for c in creatives:
        bid = c.get("generation_batch_id", "unknown")
        batches.setdefault(bid, []).append(c)

    # Within each batch, sort by variant_label A/B/C
    for bid in batches:
        batches[bid].sort(key=lambda x: x.get("variant_label", ""))

    pending_banner = ""
    if pending:
        pending_banner = f"""
        <div class="alert" style="background:rgba(59,126,197,0.08);color:#3B7EC5;">
            Generating 3 variants for batch <code>{pending[:8]}</code>...
            Refresh the page in about 15 seconds.
        </div>
        """

    if not batches:
        body = f"""
            <h1>Ad Creatives</h1>
            <div class="gold-divider"></div>
            {_ad_creatives_banner_html(msg)}
            {pending_banner}
            {_empty_state_html()}
        """
    else:
        batches_html = "".join(
            _batch_section_html(bid, items, _settings.is_meta_creative_ready)
            for bid, items in batches.items()
        )
        body = f"""
            <h1>Ad Creatives</h1>
            <div class="gold-divider"></div>
            {_ad_creatives_banner_html(msg)}
            {pending_banner}
            {batches_html}
        """

    return HTMLResponse(_base_html("Ad Creatives", body, active="ad-creatives"))


async def _run_generation_task(
    sku: str, batch_id: str, idempotency_key: str
) -> None:
    """BackgroundTask worker: Claude generate → persist variants → mark batch complete.

    Keeps errors out of the request path. Dashboard polls batch status via refresh.
    """
    from src.marketing.ad_generator import AdCreativeGenerator, AdGeneratorError

    db = _get_db()
    try:
        product = db.get_product_by_sku(sku)
        if not product:
            db.update_generation_batch_status(
                batch_id, "failed", error_message=f"Product {sku} not found"
            )
            return

        # Lazy image backfill: if Supabase row has no images, pull from Shopify now.
        # The cron /cron/sync-shopify-products does this every 15 min, but new products
        # shouldn't have to wait. Only fires when images are missing + shopify_product_id exists.
        if not product.get("images") and product.get("shopify_product_id"):
            try:
                from src.core.shopify_client import ShopifyClient
                shopify = ShopifyClient()
                try:
                    sp = await shopify.get_product(int(product["shopify_product_id"]))
                finally:
                    await shopify.close()
                if sp:
                    image_urls = [
                        img.get("src", "")
                        for img in sp.get("images", [])
                        if img.get("src")
                    ]
                    if image_urls:
                        db.update_product_images(sku, image_urls)
                        product["images"] = image_urls
                        logger.info(
                            "Lazy backfilled %d images for sku=%s from Shopify",
                            len(image_urls), sku,
                        )
            except Exception:
                logger.exception("Lazy image backfill failed for sku=%s (continuing)", sku)

        gen = AdCreativeGenerator()
        variants, returned_batch_id, dna_hash = gen.generate(product, n_variants=3)

        rows = [
            v.to_db_row(sku=sku, generation_batch_id=batch_id, brand_dna_hash=dna_hash)
            for v in variants
        ]
        db.create_ad_creative_batch(rows)
        db.update_generation_batch_status(
            batch_id, "complete", variant_count=len(variants)
        )
        logger.info(
            "Ad creative batch %s complete: %d variants for sku=%s", batch_id, len(variants), sku
        )
    except AdGeneratorError as e:
        logger.exception("Ad generation failed for batch %s", batch_id)
        db.update_generation_batch_status(batch_id, "failed", error_message=str(e))
    except Exception as e:  # pragma: no cover — catch-all for unexpected
        logger.exception("Unexpected error in ad generation batch %s", batch_id)
        db.update_generation_batch_status(batch_id, "failed", error_message=f"{type(e).__name__}: {e}")


@router.post("/ad-creatives/generate")
async def ad_creatives_generate(
    request: Request,
    dash_token: str | None = Cookie(None),
    sku: str = Form(...),
):
    """Launch a Claude generation run in the background + redirect immediately.

    Uses an idempotency key (sha1 of sku + ISO-minute) via upsert on generation_batches.
    A double-submit in the same minute returns the existing batch_id, not a new one.
    """
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    from fastapi import BackgroundTasks
    import hashlib
    import uuid
    from datetime import datetime

    # Idempotency key stable within a 1-minute window
    minute_bucket = datetime.utcnow().strftime("%Y%m%d%H%M")
    idempotency_key = hashlib.sha1(f"{sku}|{minute_bucket}".encode()).hexdigest()

    db = _get_db()
    batch_id = str(uuid.uuid4())
    existing = db.create_generation_batch({
        "id": batch_id,
        "sku": sku,
        "idempotency_key": idempotency_key,
        "status": "generating",
    })
    # If upsert returned a row with a different id, another request won the race
    actual_batch_id = existing.get("id", batch_id) if existing else batch_id

    # Only run the background task for new batches (idempotency guard)
    if actual_batch_id == batch_id:
        background_tasks = BackgroundTasks()
        background_tasks.add_task(_run_generation_task, sku, batch_id, idempotency_key)
        response = RedirectResponse(
            f"/dashboard/ad-creatives?pending={batch_id}", status_code=303
        )
        response.background = background_tasks
        return response

    return RedirectResponse(
        f"/dashboard/ad-creatives?pending={actual_batch_id}", status_code=303
    )


@router.post("/ad-creatives/{creative_id}/approve")
async def ad_creatives_approve(
    creative_id: int, dash_token: str | None = Cookie(None)
):
    """Atomic transition pending_review → publishing, push to Meta (PAUSED), mark published.

    Race-safe: two concurrent approves will see the second one return None from the
    atomic update → show "already being processed" and abort.
    """
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    from src.core.settings import settings as _settings
    if not _settings.is_meta_creative_ready:
        return RedirectResponse(
            "/dashboard/ad-creatives?msg=Error:+Meta+push+not+configured",
            status_code=303,
        )

    db = _get_db()
    transitioned = db.approve_ad_creative_atomic(creative_id, approved_by="dashboard")
    if transitioned is None:
        return RedirectResponse(
            "/dashboard/ad-creatives?msg=Error:+Already+processed+or+not+in+pending+state",
            status_code=303,
        )

    # Load the product for the Meta call (product_name, sku)
    product = db.get_product_by_sku(transitioned.get("sku", ""))
    product_name = product.get("name", transitioned.get("sku", "Pinaka")) if product else "Pinaka"

    from src.marketing.ad_generator import AdVariant
    from src.marketing.meta_creative import MetaCreativeClient, MetaCreativeError

    variant = AdVariant(
        variant_label=transitioned.get("variant_label", "A"),
        headline=transitioned.get("headline", ""),
        primary_text=transitioned.get("primary_text", ""),
        description=transitioned.get("description", ""),
        cta=transitioned.get("cta", "SHOP_NOW"),
        image_url=transitioned.get("image_url", ""),
    )

    client = MetaCreativeClient()
    try:
        result = await client.create_creative(
            variant,
            product_name=product_name,
            product_sku=transitioned.get("sku", ""),
            batch_id=transitioned.get("generation_batch_id", ""),
        )
        db.mark_ad_creative_published(creative_id, result.creative_id)
        msg = f"Approved:+pushed+to+Meta+(PAUSED):+{result.creative_id}"
    except MetaCreativeError as e:
        # Rollback: put draft back in pending_review so founder can retry
        db.revert_ad_creative_to_pending(creative_id)
        logger.exception("Meta creative push failed for creative_id=%s", creative_id)
        msg = f"Error:+Meta+push+failed:+{str(e)[:80]}"
    except Exception as e:
        db.revert_ad_creative_to_pending(creative_id)
        logger.exception("Unexpected error pushing creative_id=%s", creative_id)
        msg = f"Error:+{type(e).__name__}"

    return RedirectResponse(f"/dashboard/ad-creatives?msg={msg}", status_code=303)


@router.post("/ad-creatives/{creative_id}/reject")
async def ad_creatives_reject(
    creative_id: int, dash_token: str | None = Cookie(None)
):
    """Mark a draft rejected. No Meta call — only changes DB status."""
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    db = _get_db()
    db.reject_ad_creative(creative_id)
    return RedirectResponse(
        "/dashboard/ad-creatives?msg=Rejected", status_code=303
    )


@router.post("/ad-creatives/{creative_id}/set-live")
async def ad_creatives_set_live(
    creative_id: int, dash_token: str | None = Cookie(None)
):
    """Flip creative to ACTIVE AND create an Ad object attached to the default Ad Set.

    Phase 6.2: collapses the "attach creative to ad set" manual step. After this runs,
    the only thing between the founder and served impressions is flipping the parent
    Ad Set from PAUSED → ACTIVE in Ads Manager (one-time setup; subsequent Go Live
    clicks start serving immediately).

    Flow:
      1. POST /{creative_id}?status=ACTIVE → creative is live in Creative Library
      2. POST /act_{id}/ads with {creative_id, adset_id} → new Ad object created ACTIVE
      3. DB: status='live', meta_ad_id=<returned>, meta_adset_id=<default>
    """
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    from src.core.settings import settings as _settings
    from src.marketing.meta_creative import MetaCreativeClient, MetaCreativeError

    db = _get_db()
    creative = db.get_ad_creative(creative_id)
    if not creative or not creative.get("meta_creative_id"):
        return RedirectResponse(
            "/dashboard/ad-creatives?msg=Error:+Creative+not+found+or+never+pushed+to+Meta",
            status_code=303,
        )

    client = MetaCreativeClient()
    meta_creative_id = creative["meta_creative_id"]
    try:
        # Step 1: activate the creative asset
        await client.set_creative_status(meta_creative_id, "ACTIVE")

        # Step 2: create an Ad under the default Ad Set (Phase 6.2). Backwards-compat:
        # if no default ad set configured, skip Ad creation and just flip the creative.
        ad_id: str | None = None
        adset_id: str | None = None
        if _settings.is_meta_ad_ready:
            sku = creative.get("sku", "unknown")
            variant = creative.get("variant_label", "A")
            ad_name = f"Pinaka — {sku} — Variant {variant} — {meta_creative_id[-6:]}"
            ad_result = await client.create_ad(
                creative_id=meta_creative_id,
                ad_name=ad_name,
                status="ACTIVE",
            )
            ad_id = ad_result.ad_id
            adset_id = ad_result.adset_id

        db.set_ad_creative_live(
            creative_id, meta_ad_id=ad_id, meta_adset_id=adset_id
        )
        if ad_id:
            msg = f"LIVE:+creative+{meta_creative_id}+→+ad+{ad_id}"
        else:
            msg = f"Creative+{meta_creative_id}+is+now+LIVE+on+Meta"
    except MetaCreativeError as e:
        logger.exception("Failed to set creative %s active", creative_id)
        msg = f"Error:+{str(e)[:80]}"
    return RedirectResponse(f"/dashboard/ad-creatives?msg={msg}", status_code=303)


@router.post("/ad-creatives/{creative_id}/pause")
async def ad_creatives_pause(
    creative_id: int, dash_token: str | None = Cookie(None)
):
    """Mark a creative as paused in our DB. Does NOT call Meta.

    Meta's Ad Creative update endpoint does not support flipping ACTIVE → PAUSED
    (see meta_creative.set_creative_status docstring). Pausing at the creative
    level is a no-op on Meta's side; to stop an active creative, the founder
    must pause the Ad that uses it in Meta Ads Manager (or click "Remove from Meta"
    which DELETEs the creative).

    This button exists purely as internal bookkeeping — "I decided I don't like
    this one anymore, hide it from the dashboard list."
    """
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    db = _get_db()
    db.pause_ad_creative(creative_id)
    return RedirectResponse(
        "/dashboard/ad-creatives?msg=Archived+from+dashboard.+To+stop+ads+on+Meta,+pause+the+Ad+in+Meta+Ads+Manager",
        status_code=303,
    )


# ── Product Pipeline (PDF catalog → Pomelli → Shopify) ──

CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "catalog"
CATALOG_JSON = CATALOG_DIR / "catalog.json"
BASE_IMAGES_DIR = CATALOG_DIR / "base_images"
POMELLI_DIR = CATALOG_DIR / "pomelli_images"


def _load_catalog() -> list[dict]:
    if not CATALOG_JSON.exists():
        return []
    with open(CATALOG_JSON) as f:
        return json.load(f)


def _save_catalog(catalog: list[dict]) -> None:
    with open(CATALOG_JSON, "w") as f:
        json.dump(catalog, f, indent=2)


@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(
    request: Request,
    msg: str = "",
    dash_token: str | None = Cookie(None),
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    catalog = _load_catalog()

    alert = ""
    if msg:
        alert = f'<div class="alert alert-success">{msg}</div>'

    # Stats
    total = len(catalog)
    needs_photo = sum(1 for p in catalog if p.get("status") == "needs_photoshoot")
    ready = sum(1 for p in catalog if p.get("status") == "ready_to_review")
    published = sum(1 for p in catalog if p.get("status") == "published")

    metrics = f"""
    <div class="metrics">
        <div class="metric"><div class="metric-label">Total Products</div><div class="metric-value">{total}</div></div>
        <div class="metric"><div class="metric-label">Needs Photoshoot</div><div class="metric-value">{needs_photo}</div></div>
        <div class="metric"><div class="metric-label">Ready to Review</div><div class="metric-value">{ready}</div></div>
        <div class="metric"><div class="metric-label">Published</div><div class="metric-value">{published}</div></div>
    </div>
    """

    # Product cards
    cards = ""
    for i, p in enumerate(catalog):
        sku = p["sku"]
        status = p.get("status", "needs_photoshoot")
        pomelli_count = len(p.get("pomelli_images", []))

        # Status badge
        if status == "published":
            badge = '<span style="color:var(--success);font-size:12px;font-weight:600;">PUBLISHED</span>'
        elif status == "ready_to_review":
            badge = '<span style="color:var(--accent);font-size:12px;font-weight:600;">READY TO REVIEW</span>'
        else:
            badge = '<span style="color:var(--text-muted);font-size:12px;font-weight:600;">NEEDS PHOTOSHOOT</span>'

        # Base image as base64
        img_html = ""
        img_path = BASE_IMAGES_DIR / p.get("base_image", "")
        if img_path.exists():
            img_data = base64.b64encode(img_path.read_bytes()).decode()
            img_html = f'<img src="data:image/png;base64,{img_data}" style="width:180px;height:auto;border-radius:8px;border:1px solid var(--border);">'

        # Pomelli thumbnails
        pomelli_html = ""
        pomelli_dir = POMELLI_DIR / sku
        if pomelli_dir.exists():
            for img_file in sorted(pomelli_dir.iterdir()):
                if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    pdata = base64.b64encode(img_file.read_bytes()).decode()
                    ext = img_file.suffix.lower().replace(".", "")
                    mime = "jpeg" if ext == "jpg" else ext
                    pomelli_html += f'<img src="data:image/{mime};base64,{pdata}" style="width:80px;height:80px;object-fit:cover;border-radius:6px;border:1px solid var(--border);">'

        gem_tag = ""
        if p.get("gemstone"):
            gem_tag = f'<span class="tag" style="background:var(--accent-subtle);color:var(--accent);">{p["gemstone"]}</span>'

        cards += f"""
        <div class="card" style="display:grid;grid-template-columns:180px 1fr;gap:20px;align-items:start;">
            <div>{img_html}
                <a href="/dashboard/pipeline/{sku}/download" class="btn btn-sm" style="margin-top:8px;display:block;text-align:center;font-size:11px;">Download for Pomelli</a>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;align-items:start;">
                    <div>
                        <div class="product-name">{sku}</div>
                        <div class="product-detail">{p.get('style','')} {p.get('line_type','')} Line — {p.get('metal','')} {p.get('karat','')}</div>
                        <div class="product-detail">{p.get('carats','')} — {p.get('weight_gm','')}g</div>
                    </div>
                    <div style="text-align:right;">
                        {badge}
                    </div>
                </div>
                <div class="product-tags" style="margin-top:8px;">
                    <span class="tag">{p.get('style','')}</span>
                    <span class="tag">{p.get('line_type','')} Line</span>
                    <span class="tag">{p.get('metal','')}</span>
                    <span class="tag">{p.get('carats','')}</span>
                    {gem_tag}
                </div>

                <div style="margin-top:16px;">
                    <h3>Pomelli Images ({pomelli_count})</h3>
                    <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
                        {pomelli_html if pomelli_html else '<span style="font-size:13px;color:var(--text-muted);">No images yet — download base image, run through Pomelli, then upload here</span>'}
                    </div>
                    <form action="/dashboard/pipeline/{sku}/upload" method="post" enctype="multipart/form-data" style="margin-top:12px;display:flex;gap:8px;align-items:center;">
                        <input type="file" name="images" accept="image/*" multiple style="font-size:13px;">
                        <button type="submit" class="btn btn-sm btn-primary">Upload</button>
                    </form>
                </div>

                {f'''<div style="margin-top:16px;">
                    <form action="/dashboard/pipeline/{sku}/publish" method="post" style="display:inline;">
                        <button type="submit" class="btn btn-primary" {"disabled" if pomelli_count == 0 else ""}>Create on Shopify</button>
                    </form>
                </div>''' if status != "published" else '<div style="margin-top:12px;font-size:13px;color:var(--success);">Live on Shopify</div>'}
            </div>
        </div>
        """

    body = f"""
    <h1>Product Pipeline</h1>
    <p style="color:var(--text-muted);margin:4px 0 24px;">PDF Catalog → Base Image → Pomelli Photoshoot → Shopify</p>
    {alert}
    {metrics}
    <div class="gold-divider"></div>
    {cards if cards else '<p style="color:var(--text-muted);">No products in catalog. Run the PDF extraction script first.</p>'}
    """
    return _base_html("Product Pipeline", body, active="pipeline")


@router.get("/pipeline/{sku}/download")
async def pipeline_download(sku: str, dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    img_path = BASE_IMAGES_DIR / f"{sku}.png"
    if not img_path.exists():
        return RedirectResponse("/dashboard/pipeline?msg=Image+not+found", status_code=303)

    from fastapi.responses import FileResponse
    return FileResponse(img_path, filename=f"{sku}.png", media_type="image/png")


@router.post("/pipeline/{sku}/upload")
async def pipeline_upload(
    sku: str,
    images: list[UploadFile] = File(...),
    dash_token: str | None = Cookie(None),
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    catalog = _load_catalog()
    product = next((p for p in catalog if p["sku"] == sku), None)
    if not product:
        return RedirectResponse("/dashboard/pipeline?msg=Product+not+found", status_code=303)

    # Save uploaded Pomelli images
    save_dir = POMELLI_DIR / sku
    save_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for img in images:
        if not img.filename:
            continue
        ext = Path(img.filename).suffix.lower() or ".png"
        filename = f"{sku}_pomelli_{saved + 1}{ext}"
        filepath = save_dir / filename
        content = await img.read()
        filepath.write_bytes(content)

        if filename not in product.get("pomelli_images", []):
            product.setdefault("pomelli_images", []).append(filename)
        saved += 1

    if saved > 0:
        product["status"] = "ready_to_review"
        _save_catalog(catalog)

    return RedirectResponse(
        f"/dashboard/pipeline?msg=Uploaded+{saved}+images+for+{sku}",
        status_code=303,
    )


@router.post("/pipeline/{sku}/publish")
async def pipeline_publish(
    sku: str,
    dash_token: str | None = Cookie(None),
):
    """Create a Shopify product from catalog data + Pomelli images."""
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    catalog = _load_catalog()
    product = next((p for p in catalog if p["sku"] == sku), None)
    if not product:
        return RedirectResponse("/dashboard/pipeline?msg=Product+not+found", status_code=303)

    # Collect all images (base + pomelli)
    image_paths = []
    base_path = BASE_IMAGES_DIR / product.get("base_image", "")
    if base_path.exists():
        image_paths.append(base_path)

    pomelli_dir = POMELLI_DIR / sku
    if pomelli_dir.exists():
        for img_file in sorted(pomelli_dir.iterdir()):
            if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                image_paths.append(img_file)

    if not image_paths:
        return RedirectResponse("/dashboard/pipeline?msg=No+images+to+upload", status_code=303)

    # Build product title
    style = product.get("style", "")
    line_type = product.get("line_type", "Single")
    metal = product.get("metal", "")
    carats = product.get("carats", "")
    gemstone = product.get("gemstone", "")

    if gemstone:
        title = f"{gemstone} & Diamond Tennis Bracelet — {style} Set {carats}"
    else:
        title = f"Diamond Tennis Bracelet — {style} {line_type} Line {carats}"

    # Build description
    description = (
        f"<p>Handcrafted {product.get('karat', '14K')} {metal.lower()} {style.lower()} set "
        f"diamond tennis bracelet. {carats} total carat weight, {product.get('weight_gm', '')}g. "
        f"Made to order in 15 business days.</p>"
    )

    # Create on Shopify
    shopify_token = settings.shopify_access_token
    shop = settings.shopify_shop_domain or "pinaka-jewellery.myshopify.com"

    # Upload images as base64
    shopify_images = []
    for i, img_path in enumerate(image_paths):
        img_b64 = base64.b64encode(img_path.read_bytes()).decode()
        shopify_images.append({
            "attachment": img_b64,
            "filename": img_path.name,
            "position": i + 1,
        })

    payload = {
        "product": {
            "title": title,
            "body_html": description,
            "vendor": "Pinaka Jewellery",
            "product_type": "Bracelet",
            "tags": f"{sku}, {style}, {line_type} Line, {metal}, {product.get('karat', '14K')}, {carats}" + (f", {gemstone}" if gemstone else ""),
            "status": "draft",
            "images": shopify_images,
            "variants": [
                {
                    "title": "Default",
                    "sku": sku,
                    "inventory_management": None,
                    "inventory_policy": "continue",
                }
            ],
        }
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"https://{shop}/admin/api/2025-01/products.json",
            headers={
                "X-Shopify-Access-Token": shopify_token,
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code in (200, 201):
        shopify_product = resp.json().get("product", {})
        product["status"] = "published"
        product["shopify_product_id"] = shopify_product.get("id")
        _save_catalog(catalog)
        return RedirectResponse(
            f"/dashboard/pipeline?msg={sku}+created+on+Shopify+as+draft!+ID:+{shopify_product.get('id', 'unknown')}",
            status_code=303,
        )
    else:
        error = resp.text[:200]
        return RedirectResponse(
            f"/dashboard/pipeline?msg=Shopify+error:+{error}",
            status_code=303,
        )
