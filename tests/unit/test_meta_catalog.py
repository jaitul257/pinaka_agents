"""Tests for Meta Product Catalog Batch API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.marketing.meta_catalog import (
    CatalogSyncResult,
    MetaCatalogSync,
    _get_retail_price,
    _slugify,
    map_product_to_catalog_item,
)


def _make_product(**overrides) -> dict:
    """Build a realistic product dict matching Supabase products table."""
    product = {
        "id": 1,
        "sku": "DTB-LG-7-14KYG",
        "name": "Diamond Tennis Bracelet - Lab Grown",
        "category": "Bracelets",
        "materials": {
            "metal": "14K Yellow Gold",
            "weight_grams": 12.5,
            "diamond_type": ["lab-grown", "VS1-VS2", "F-G color"],
            "total_carat": 3.0,
        },
        "pricing": {
            "default-7inch": {"cost": 450.0, "retail": 2850.0},
        },
        "story": "Handcrafted lab-grown diamond bracelet with timeless elegance.",
        "care_instructions": "Clean with warm water and mild soap.",
        "occasions": ["anniversary", "birthday"],
        "certification": {
            "certificate_number": "LG-2026-0001",
            "grading_lab": "IGI",
            "carat_weight_certified": 3.0,
            "clarity": "VS1",
            "color": "F",
        },
        "images": [
            "https://cdn.shopify.com/s/files/1/dtb-lg-main.jpg",
            "https://cdn.shopify.com/s/files/1/dtb-lg-side.jpg",
        ],
        "tags": ["diamond", "bracelet", "lab-grown", "tennis"],
        "shopify_product_id": 9876543210,
    }
    product.update(overrides)
    return product


# ── Unit: map_product_to_catalog_item ──


def test_map_product_full():
    """Product with all fields should map to correct Meta catalog format."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.storefront_domain = "pinakajewellery.com"
        item = map_product_to_catalog_item(_make_product())

    assert item is not None
    assert item["method"] == "UPDATE"
    assert item["retailer_id"] == "DTB-LG-7-14KYG"

    data = item["data"]
    assert data["name"] == "Diamond Tennis Bracelet - Lab Grown"
    assert data["price"] == "2850.00 USD"
    assert data["availability"] == "in stock"
    assert data["condition"] == "new"
    assert data["brand"] == "Pinaka Jewellery"
    assert data["google_product_category"] == "Apparel & Accessories > Jewelry"
    assert data["custom_label_0"] == "Bracelets"
    assert data["image_link"] == "https://cdn.shopify.com/s/files/1/dtb-lg-main.jpg"
    assert "pinakajewellery.com/products/" in data["link"]
    assert "diamond-tennis-bracelet" in data["link"]
    assert "14K Yellow Gold" in data["description"]
    assert "3.0 total carat" in data["description"]


def test_map_product_no_images():
    """Product with empty images should have empty image_link."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.shopify_shop_domain = "test.myshopify.com"
        item = map_product_to_catalog_item(_make_product(images=[]))

    assert item is not None
    assert item["data"]["image_link"] == ""


def test_map_product_no_pricing():
    """Product with null/empty pricing should be skipped (returns None)."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.shopify_shop_domain = "test.myshopify.com"

        assert map_product_to_catalog_item(_make_product(pricing=None)) is None
        assert map_product_to_catalog_item(_make_product(pricing={})) is None


def test_map_product_missing_name():
    """Product without a name should be skipped."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.shopify_shop_domain = "test.myshopify.com"
        assert map_product_to_catalog_item(_make_product(name="")) is None


def test_slugify():
    """Should convert product names to URL-friendly handles."""
    assert _slugify("Diamond Tennis Bracelet - Lab Grown") == "diamond-tennis-bracelet-lab-grown"
    assert _slugify("14K Gold Ring (Size 7)") == "14k-gold-ring-size-7"
    assert _slugify("  Spaces  ") == "spaces"


def test_get_retail_price():
    """Should extract retail price from pricing JSONB."""
    assert _get_retail_price({"default": {"cost": 100, "retail": 250}}) == "250.00 USD"
    assert _get_retail_price({}) == ""
    assert _get_retail_price(None) == ""
    assert _get_retail_price({"variant": {"cost": 50}}) == ""  # no retail key


# ── Integration: MetaCatalogSync.sync_products ──


@pytest.fixture
def catalog_sync():
    """Create MetaCatalogSync with configured settings."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.meta_capi_access_token = "test-token"
        mock_settings.meta_catalog_id = "catalog_123"
        mock_settings.meta_graph_api_version = "v21.0"
        mock_settings.shopify_shop_domain = "pinaka-jewellery.myshopify.com"
        yield MetaCatalogSync()


def _mock_response(status_code: int = 200, json_data: dict = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data)
    return resp


async def test_sync_catalog_success(catalog_sync):
    """Successful batch sync should report correct items_synced count."""
    products = [_make_product(sku=f"SKU-{i}", name=f"Product {i}") for i in range(3)]

    api_response = {"handles": ["h1", "h2", "h3"], "validation_status": []}
    mock_resp = _mock_response(200, api_response)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_catalog.httpx.AsyncClient", return_value=mock_client):
        result = await catalog_sync.sync_products(products)

    assert isinstance(result, CatalogSyncResult)
    assert result.items_synced == 3
    assert result.items_failed == 0
    assert result.status == "ok"


async def test_sync_catalog_partial_failure(catalog_sync):
    """Some items failing in batch should report partial_failure."""
    products = [_make_product(sku=f"SKU-{i}", name=f"Product {i}") for i in range(3)]

    # Only 2 of 3 succeed
    api_response = {
        "handles": ["h1", "h2"],
        "validation_status": [
            {"retailer_id": "SKU-2", "errors": [{"message": "Invalid image URL"}]}
        ],
    }
    mock_resp = _mock_response(200, api_response)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_catalog.httpx.AsyncClient", return_value=mock_client):
        result = await catalog_sync.sync_products(products)

    assert result.items_synced == 2
    assert result.items_failed == 1
    assert result.status == "partial_failure"


async def test_sync_catalog_empty_products(catalog_sync):
    """Empty product list should skip API call entirely."""
    result = await catalog_sync.sync_products([])

    assert result.items_synced == 0
    assert result.items_failed == 0
    assert result.status == "skipped"


async def test_sync_catalog_api_error(catalog_sync):
    """Non-200 from Meta should report all items as failed."""
    products = [_make_product()]

    mock_resp = _mock_response(500, {"error": {"message": "Internal error"}})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("src.marketing.meta_catalog.httpx.AsyncClient", return_value=mock_client):
        result = await catalog_sync.sync_products(products)

    assert result.items_synced == 0
    assert result.items_failed == 1
    assert len(result.errors) == 1
    assert "500" in result.errors[0]


async def test_sync_catalog_not_configured():
    """Unconfigured client should return error without making API calls."""
    with patch("src.marketing.meta_catalog.settings") as mock_settings:
        mock_settings.meta_capi_access_token = ""
        mock_settings.meta_catalog_id = ""
        mock_settings.meta_graph_api_version = "v21.0"
        sync = MetaCatalogSync()

    result = await sync.sync_products([_make_product()])
    assert result.status != "ok"
    assert len(result.errors) == 1
    assert "not configured" in result.errors[0].lower()
