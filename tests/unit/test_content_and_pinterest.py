"""Unit tests for Phase 9.3 content/retention engine."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.content.seo_writer import (
    SEOPostDraft,
    SEOWriter,
    _inline_md,
    _markdown_to_html,
    _slugify,
)
from src.marketing.pinterest import PinterestClient, PinDraft


# ── SEOWriter ──

@pytest.fixture
def writer():
    with patch("src.content.seo_writer.AsyncDatabase") as mock_db_cls, \
         patch("src.content.seo_writer.anthropic.AsyncAnthropic"):
        mock_db = AsyncMock()
        mock_db._sync._client = MagicMock()
        mock_db_cls.return_value = mock_db
        w = SEOWriter()
        yield w


def _claude_response(payload: dict):
    resp = MagicMock()
    msg = MagicMock()
    msg.text = json.dumps(payload)
    resp.content = [msg]
    return resp


def test_slugify_basic():
    assert _slugify("Diamond Tennis Bracelet for 10 Year Anniversary") \
        == "diamond-tennis-bracelet-for-10-year-anniversary"


def test_slugify_strips_punctuation():
    assert _slugify("What's the 4Cs?") == "whats-the-4cs"


def test_slugify_collapses_dashes():
    assert _slugify("  hello  --  world  ") == "hello-world"


def test_markdown_to_html_downshifts_h1():
    """Shopify article body shouldn't have H1 (title is separate) — H1 → H2."""
    md = "# Title\n\nText paragraph.\n\n## Sub\n\n- bullet one\n- bullet two"
    html = _markdown_to_html(md)
    assert "<h2>Title</h2>" in html
    assert "<h3>Sub</h3>" in html
    assert "<ul>" in html and "<li>bullet one</li>" in html


def test_inline_md_bold_italic():
    html = _inline_md("**bold** and *italic* and `code`")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html


@pytest.mark.asyncio
async def test_draft_parses_claude_json(writer):
    payload = {
        "title": "Diamond Tennis Bracelet for 10 Year Anniversary",
        "meta_description": "A fifty-word thing about anniversary bracelets that is about 150 chars long on purpose so it fits.",
        "slug": "tennis-bracelet-10-year-anniversary",
        "tags": ["anniversary", "tennis bracelet"],
        "body_markdown": "# Title\n\nFirst paragraph. Quite short.\n\n## Section\n\nMore text.",
    }
    writer._claude.messages.create = AsyncMock(return_value=_claude_response(payload))

    draft = await writer.draft("diamond tennis bracelet for 10 year anniversary", "anniversary")
    assert draft.title == payload["title"]
    assert draft.slug == "tennis-bracelet-10-year-anniversary"
    assert "anniversary" in draft.tags
    assert "<h2>" in draft.body_html  # H1 downshifted
    assert draft.word_count > 0


@pytest.mark.asyncio
async def test_draft_raises_on_no_json(writer):
    mock_resp = MagicMock()
    mock_msg = MagicMock(text="Just some prose, no JSON here")
    mock_resp.content = [mock_msg]
    writer._claude.messages.create = AsyncMock(return_value=mock_resp)

    with pytest.raises(RuntimeError):
        await writer.draft("keyword", "category")


@pytest.mark.asyncio
async def test_shopify_publish_skips_without_config(writer):
    with patch("src.content.seo_writer.settings") as s:
        s.shopify_shop_domain = ""
        s.shopify_access_token = ""
        s.shopify_blog_id = ""
        s.shopify_api_version = "2025-01"
        draft = SEOPostDraft(
            keyword="x", category="x", title="T", meta_description="M",
            slug="t", body_html="<p>H</p>", body_markdown="H",
            tags=[], word_count=5,
        )
        result = await writer.publish_draft(draft)
        assert result.publish_error is not None
        assert "SHOPIFY_BLOG_ID" in result.publish_error


@pytest.mark.asyncio
async def test_shopify_publish_handles_403_scope(writer):
    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 403
    mock_resp.text = "Unauthorized"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("src.content.seo_writer.settings") as s, \
         patch("httpx.AsyncClient", return_value=mock_client):
        s.shopify_shop_domain = "pinaka-jewellery.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_blog_id = "123"
        s.shopify_api_version = "2025-01"
        draft = SEOPostDraft(
            keyword="x", category="x", title="T", meta_description="M",
            slug="t", body_html="<p>H</p>", body_markdown="H",
            tags=[], word_count=5,
        )
        result = await writer.publish_draft(draft)
        assert result.shopify_article_id is None
        assert "write_content" in (result.publish_error or "")


@pytest.mark.asyncio
async def test_shopify_publish_success(writer):
    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 201
    mock_resp.text = ""
    mock_resp.json.return_value = {"article": {"id": 987654321, "handle": "x"}}

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("src.content.seo_writer.settings") as s, \
         patch("httpx.AsyncClient", return_value=mock_client):
        s.shopify_shop_domain = "pinaka-jewellery.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_blog_id = "123"
        s.shopify_api_version = "2025-01"
        draft = SEOPostDraft(
            keyword="x", category="x", title="T", meta_description="M",
            slug="t", body_html="<p>H</p>", body_markdown="H",
            tags=["a", "b"], word_count=5,
        )
        result = await writer.publish_draft(draft)
        assert result.shopify_article_id == 987654321
        assert "987654321" in (result.shopify_admin_url or "")


# ── PinterestClient ──

@pytest.fixture
def pin_client():
    with patch("src.marketing.pinterest.AsyncDatabase") as mock_db_cls, \
         patch("src.marketing.pinterest.anthropic.AsyncAnthropic"), \
         patch("src.marketing.pinterest.settings") as s:
        s.anthropic_api_key = "test"
        s.claude_model = "claude-sonnet-4"
        s.pinterest_access_token = "token123"
        s.pinterest_board_id = "board456"
        s.shopify_storefront_url = "https://pinakajewellery.com"
        mock_db_cls.return_value = AsyncMock()
        yield PinterestClient()


def test_pinterest_unconfigured():
    with patch("src.marketing.pinterest.settings") as s:
        s.pinterest_access_token = ""
        s.pinterest_board_id = ""
        s.anthropic_api_key = ""
        c = PinterestClient()
        assert c.is_configured is False


@pytest.mark.asyncio
async def test_pick_product_rotates(pin_client):
    pin_client._db.get_all_products = AsyncMock(return_value=[
        {"name": "A", "status": "active"},
        {"name": "B", "status": "active"},
        {"name": "C", "status": "active"},
    ])
    p0 = await pin_client.pick_product(day_index=0)
    p1 = await pin_client.pick_product(day_index=1)
    p2 = await pin_client.pick_product(day_index=2)
    p3 = await pin_client.pick_product(day_index=3)
    assert p0["name"] == "A"
    assert p1["name"] == "B"
    assert p2["name"] == "C"
    assert p3["name"] == "A"  # wraps


@pytest.mark.asyncio
async def test_pick_product_empty(pin_client):
    pin_client._db.get_all_products = AsyncMock(return_value=[])
    assert await pin_client.pick_product() is None


@pytest.mark.asyncio
async def test_draft_copy_requires_https_image(pin_client):
    """Pinterest rejects non-HTTPS image URLs; we pre-filter."""
    product = {
        "name": "Test Bracelet",
        "handle": "test-bracelet",
        "images": [{"src": "http://insecure.com/img.jpg"}],
    }
    result = await pin_client.draft_copy(product)
    assert result is None


@pytest.mark.asyncio
async def test_draft_copy_with_claude(pin_client):
    product = {
        "name": "Diamond Tennis Bracelet — Yellow Gold",
        "handle": "diamond-tennis-yellow-gold",
        "images": [{"src": "https://cdn.shopify.com/bracelet.jpg"}],
        "materials": {"metal": "yellow-gold"},
        "story": "Handcrafted in our atelier.",
    }
    mock_resp = MagicMock()
    mock_msg = MagicMock(text=json.dumps({
        "title": "Yellow Gold Diamond Tennis Bracelet — Made to Order",
        "description": "Description 400 chars long about bracelets and anniversaries.",
        "alt_text": "Yellow gold diamond tennis bracelet on a cream background.",
    }))
    mock_resp.content = [mock_msg]
    pin_client._claude.messages.create = AsyncMock(return_value=mock_resp)

    draft = await pin_client.draft_copy(product)
    assert draft is not None
    assert "Yellow Gold" in draft.title
    assert draft.image_url == "https://cdn.shopify.com/bracelet.jpg"
    assert "pinakajewellery.com/products/diamond-tennis-yellow-gold" in draft.product_url


@pytest.mark.asyncio
async def test_create_pin_unconfigured_sets_error(pin_client):
    pin_client._token = ""
    pin_client._board_id = ""
    draft = PinDraft(
        product_name="x", product_url="https://y.com", image_url="https://z.com/a.jpg",
        title="t", description="d", alt_text="a",
    )
    result = await pin_client.create_pin(draft)
    assert result.pin_id is None
    assert "PINTEREST_ACCESS_TOKEN" in (result.error or "")


@pytest.mark.asyncio
async def test_create_pin_success(pin_client):
    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 201
    mock_resp.text = ""
    mock_resp.json.return_value = {"id": "654321987654321"}

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        draft = PinDraft(
            product_name="x", product_url="https://y.com", image_url="https://z.com/a.jpg",
            title="t", description="d", alt_text="a",
        )
        result = await pin_client.create_pin(draft)
    assert result.pin_id == "654321987654321"


@pytest.mark.asyncio
async def test_create_pin_api_error(pin_client):
    import httpx
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 400
    mock_resp.text = "Invalid board_id"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        draft = PinDraft(
            product_name="x", product_url="https://y.com", image_url="https://z.com/a.jpg",
            title="t", description="d", alt_text="a",
        )
        result = await pin_client.create_pin(draft)
    assert result.pin_id is None
    assert "400" in (result.error or "")
