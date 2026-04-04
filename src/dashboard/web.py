"""Pinaka Admin Dashboard — HTML pages served from FastAPI.

Product catalog management, password-gated. Styled per DESIGN.md.
"""

import base64
import hmac
import json
import logging

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

    metals = ["14K Yellow Gold", "14K White Gold", "14K Rose Gold", "18K Yellow Gold", "18K White Gold", "18K Rose Gold", "Platinum", "Sterling Silver"]
    metal_options = "".join(f'<option value="{m}" {"selected" if m == materials.get("metal", "14K Yellow Gold") else ""}>{m}</option>' for m in metals)

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
            <h3>Pricing</h3>
            <div class="form-grid" style="margin-top:12px;">
                <div class="form-group">
                    <label>Variant Name</label>
                    <input type="text" name="variant_name" value="{first_variant_name}">
                </div>
                <div class="form-group" style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                    <div>
                        <label>Retail Price ($) *</label>
                        <input type="number" name="retail" value="{first_variant.get("retail", 2850)}" step="1" min="0" required>
                    </div>
                    <div>
                        <label>Cost ($, private) *</label>
                        <input type="number" name="cost" value="{first_variant.get("cost", 450)}" step="1" min="0" required>
                    </div>
                </div>
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
            <div style="margin-top:12px; display:flex; gap:24px;">
                <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer;">
                    <input type="checkbox" name="embed" value="1" checked> Embed for customer service search
                </label>
                <label style="display:flex; align-items:center; gap:8px; font-size:14px; cursor:pointer;">
                    <input type="checkbox" name="push_shopify" value="1" checked> Create in Shopify (active)
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


@router.post("/add")
async def add_product_submit(
    dash_token: str | None = Cookie(None),
    name: str = Form(""),
    sku: str = Form(""),
    category: str = Form("Bracelets"),
    metal: str = Form("14K Yellow Gold"),
    total_carat: float = Form(3.0),
    diamond_type: str = Form(""),
    weight_grams: float = Form(12.5),
    variant_name: str = Form("default-7inch"),
    retail: float = Form(0),
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
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    if not name or not sku or not story:
        return RedirectResponse("/dashboard/add", status_code=303)

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

    # Push to Shopify as active product
    shopify_msg = ""
    if push_shopify and settings.shopify_shop_domain and settings.shopify_access_token:
        try:
            tags_list = list(product_data.get("tags", []))
            tags_list.append(sku)
            shopify_payload = {
                "product": {
                    "title": name,
                    "body_html": f"<p>{story}</p><p><strong>Care:</strong> {care}</p>",
                    "vendor": "Pinaka Jewellery",
                    "product_type": category,
                    "tags": ", ".join(tags_list),
                    "status": "active",
                    "variants": [
                        {
                            "title": variant_name,
                            "price": str(retail),
                            "sku": sku,
                            "inventory_management": None,
                        }
                    ],
                }
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    _shopify_api("products.json"),
                    headers=_shopify_headers(),
                    json=shopify_payload,
                )
                if resp.status_code in (200, 201):
                    shopify_id = resp.json().get("product", {}).get("id", "")
                    if shopify_id:
                        _get_db().upsert_product({"sku": sku, "name": name, "shopify_product_id": shopify_id})
                        # Set Google Shopping metafields so Merchant Center accepts it
                        await _upsert_google_metafields(shopify_id, sku, category)
                    shopify_msg = f"+and+created+in+Shopify+(ID:+{shopify_id})"
                    logger.info("Product %s pushed to Shopify (ID: %s)", sku, shopify_id)
                else:
                    error_detail = resp.json().get("errors", resp.text[:200])
                    shopify_msg = f"+but+Shopify+push+failed:+{error_detail}"
                    logger.error("Shopify push failed for %s: %s", sku, resp.text[:300])
        except Exception as e:
            shopify_msg = f"+but+Shopify+push+failed:+{e}"
            logger.exception("Shopify push failed for %s", sku)

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+saved{shopify_msg}", status_code=303)


# ── Edit Product ──

@router.get("/edit/{sku}", response_class=HTMLResponse)
async def edit_product_page(sku: str, dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    product = _get_db().get_product_by_sku(sku)
    if not product:
        return RedirectResponse("/dashboard?msg=Product+not+found", status_code=303)

    body = f"""
        <h1>Edit Product</h1>
        <div class="gold-divider"></div>
        {_product_form(f"/dashboard/edit/{sku}", product, button_text="Update Product")}
    """
    return HTMLResponse(_base_html("Edit Product", body, active="products"))


@router.post("/edit/{sku}")
async def edit_product_submit(
    sku: str,
    dash_token: str | None = Cookie(None),
    name: str = Form(""),
    category: str = Form("Bracelets"),
    metal: str = Form("14K Yellow Gold"),
    total_carat: float = Form(3.0),
    diamond_type: str = Form(""),
    weight_grams: float = Form(12.5),
    variant_name: str = Form("default-7inch"),
    retail: float = Form(0),
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
):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

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
        # Find Shopify product ID by SKU
        existing = _get_db().get_product_by_sku(sku)
        shopify_id = (existing or {}).get("shopify_product_id")

        if shopify_id:
            # Update existing Shopify product
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
                    }
                }
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.put(
                        _shopify_api(f"products/{shopify_id}.json"),
                        headers=_shopify_headers(),
                        json=update_payload,
                    )
                    if resp.status_code == 200:
                        # Keep Google Shopping metafields in sync
                        await _upsert_google_metafields(shopify_id, sku, category)
                        shopify_msg = "+and+updated+in+Shopify"
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


async def _upsert_google_metafields(product_id: int, sku: str, category: str) -> None:
    """Set Google Shopping metafields on a Shopify product so Google Merchant Center
    accepts it without a real GTIN. Handmade jewelry uses: custom_product=TRUE,
    mpn=<sku>, brand=<vendor>, condition=new, and a specific google_product_category.
    Safe to call on create or edit — existing metafields are updated in place.
    """
    google_cat = _GOOGLE_CATEGORY_IDS.get(category, "188")  # 188 = generic Jewelry
    desired = [
        ("mm-google-shopping", "mpn", sku),
        ("mm-google-shopping", "condition", "new"),
        ("mm-google-shopping", "custom_product", "TRUE"),
        ("mm-google-shopping", "google_product_category", google_cat),
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
