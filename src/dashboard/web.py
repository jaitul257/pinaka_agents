"""Pinaka Admin Dashboard — HTML pages served from FastAPI.

Product catalog management, password-gated. Styled per DESIGN.md.
"""

import hmac
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

PRODUCTS_DIR = Path("./data/products")

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
        .btn-primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
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
        button {{ width:100%; padding:10px; background:#D4A017; color:#fff; border:none; border-radius:8px; font-family:'DM Sans',sans-serif; font-weight:500; font-size:14px; cursor:pointer; }}
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

    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)

    products = []
    for path in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                prod = json.load(f)
                prod["_file"] = path.stem
                products.append(prod)
        except Exception:
            pass

    # Metrics
    total = len(products)
    categories = len(set(p.get("category", "") for p in products))
    try:
        from src.product.embeddings import ProductEmbeddings
        embedded = ProductEmbeddings().product_count()
    except Exception:
        embedded = "—"

    alert = ""
    if msg:
        alert = f'<div class="alert alert-success">{msg}</div>'

    # Product list HTML
    product_cards = ""
    for p in products:
        pricing = p.get("pricing", {})
        first_variant = next(iter(pricing.values()), {})
        retail = first_variant.get("retail", 0)
        cost = first_variant.get("cost", 0)
        margin = ((retail - cost) / retail * 100) if retail > 0 else 0
        materials = p.get("materials", {})
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in p.get("tags", [])[:6])
        cert = p.get("certification", {})
        cert_text = f'{cert.get("grading_lab", "")} #{cert.get("certificate_number", "")}' if cert else "None"
        sku = p.get("sku", "")

        product_cards += f"""
        <div class="product">
            <div>
                <div class="product-name">{p.get("name", "Unnamed")}</div>
                <div class="product-detail">
                    SKU: {sku} &nbsp;·&nbsp; {p.get("category", "")} &nbsp;·&nbsp;
                    {materials.get("metal", "")} &nbsp;·&nbsp; {materials.get("total_carat", "")}ct &nbsp;·&nbsp;
                    Cert: {cert_text}
                </div>
                <div class="product-detail" style="margin-top:6px; font-style:italic;">
                    {p.get("story", "")[:150]}...
                </div>
                <div class="product-tags">{tags_html}</div>
            </div>
            <div style="text-align:right;">
                <div class="product-price">${retail:,.0f}</div>
                <div class="product-margin">{margin:.0f}% margin</div>
                <div style="margin-top:12px;">
                    <a href="/dashboard/edit/{sku}" class="btn btn-sm">Edit</a>
                    <form class="delete-form" method="post" action="/dashboard/delete/{sku}"
                          onsubmit="return confirm('Delete {p.get("name", "")}?')">
                        <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                    </form>
                </div>
            </div>
        </div>"""

    if not products:
        product_cards = '<div class="card"><p style="text-align:center; padding:24px;">No products yet. Add your first product to get started.</p></div>'

    body = f"""
        {alert}
        <h1>Products</h1>
        <div class="gold-divider"></div>
        <div class="metrics">
            <div class="metric"><div class="metric-label">Products</div><div class="metric-value">{total}</div></div>
            <div class="metric"><div class="metric-label">Embedded for Search</div><div class="metric-value">{embedded}</div></div>
            <div class="metric"><div class="metric-label">Categories</div><div class="metric-value">{categories}</div></div>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <h3>Your Products</h3>
            <a href="/dashboard/add" class="btn btn-primary">+ Add Product</a>
        </div>
        {product_cards}
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
                        <input type="number" name="retail" value="{first_variant.get("retail", 2850)}" step="50" min="0" required>
                    </div>
                    <div>
                        <label>Cost ($, private) *</label>
                        <input type="number" name="cost" value="{first_variant.get("cost", 450)}" step="10" min="0" required>
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
                    <input type="checkbox" name="push_shopify" value="1" checked> Create as draft in Shopify
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

    # Save to file
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = PRODUCTS_DIR / f"{sku}.json"
    filepath.write_text(json.dumps(product_data, indent=2))

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

    # Push to Shopify as draft
    if push_shopify:
        try:
            from src.core.shopify_client import ShopifyClient
            shopify = ShopifyClient()
            tags_list = product_data.get("tags", [])
            tags_list.append(sku)  # Include SKU as tag for lookup
            result = await shopify.create_product(
                title=name,
                body_html=f"<p>{story}</p><p><strong>Care:</strong> {care}</p>",
                tags=tags_list,
                product_type=category,
            )
            await shopify.close()
            shopify_id = result.get("id", "")
            logger.info("Product %s pushed to Shopify as draft (ID: %s)", sku, shopify_id)
        except Exception:
            logger.exception("Shopify push failed for %s", sku)

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+saved+successfully", status_code=303)


# ── Edit Product ──

@router.get("/edit/{sku}", response_class=HTMLResponse)
async def edit_product_page(sku: str, dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    filepath = PRODUCTS_DIR / f"{sku}.json"
    if not filepath.exists():
        return RedirectResponse("/dashboard?msg=Product+not+found", status_code=303)

    with open(filepath) as f:
        product = json.load(f)

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

    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = PRODUCTS_DIR / f"{sku}.json"
    filepath.write_text(json.dumps(product_data, indent=2))

    if embed:
        try:
            from src.product.embeddings import ProductEmbeddings
            from src.product.schema import Product
            emb = ProductEmbeddings()
            product_obj = Product(**product_data)
            emb.embed_product(product_obj)
        except Exception:
            logger.exception("Embedding failed for %s", sku)

    if push_shopify:
        try:
            from src.core.shopify_client import ShopifyClient
            shopify = ShopifyClient()
            tags_list = product_data.get("tags", [])
            tags_list.append(sku)
            result = await shopify.create_product(
                title=name,
                body_html=f"<p>{story}</p><p><strong>Care:</strong> {care}</p>",
                tags=tags_list,
                product_type=category,
            )
            await shopify.close()
            logger.info("Product %s pushed to Shopify as draft (ID: %s)", sku, result.get("id", ""))
        except Exception:
            logger.exception("Shopify push failed for %s", sku)

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+updated", status_code=303)


# ── Delete Product ──

@router.post("/delete/{sku}")
async def delete_product(sku: str, dash_token: str | None = Cookie(None)):
    if not _check_auth(dash_token):
        return RedirectResponse("/dashboard/login", status_code=303)

    filepath = PRODUCTS_DIR / f"{sku}.json"
    if filepath.exists():
        filepath.unlink()

    return RedirectResponse(f"/dashboard?msg=Product+{sku}+deleted", status_code=303)
