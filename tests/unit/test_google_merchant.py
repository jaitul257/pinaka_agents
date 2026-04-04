"""Tests for Google Merchant Center Content API client."""

from unittest.mock import MagicMock, patch

import pytest

from src.marketing.google_merchant import (
    GoogleMerchantSync,
    MerchantSyncResult,
    map_product_to_merchant_item,
    _get_retail_price,
    _slugify,
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
            "diamond_type": ["lab-grown", "VS1-VS2"],
            "total_carat": 3.0,
        },
        "pricing": {
            "default-7inch": {"cost": 450.0, "retail": 2850.0},
        },
        "story": "Handcrafted lab-grown diamond bracelet.",
        "images": [
            "https://cdn.shopify.com/s/files/1/dtb-lg-main.jpg",
        ],
        "tags": ["diamond", "bracelet"],
        "shopify_product_id": 9876543210,
    }
    product.update(overrides)
    return product


# ── Unit: map_product_to_merchant_item ──


def test_map_product_full():
    """Product with all fields should map to correct Merchant format."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.storefront_domain = "pinakajewellery.com"
        item = map_product_to_merchant_item(_make_product())

    assert item is not None
    assert item["offerId"] == "DTB-LG-7-14KYG"
    assert item["title"] == "Diamond Tennis Bracelet - Lab Grown"
    assert item["price"] == {"value": "2850.0", "currency": "USD"}
    assert item["availability"] == "in stock"
    assert item["condition"] == "new"
    assert item["brand"] == "Pinaka Jewellery"
    assert item["googleProductCategory"] == "Apparel & Accessories > Jewelry"
    assert item["productType"] == "Bracelets"
    assert item["channel"] == "online"
    assert item["targetCountry"] == "US"
    assert "pinakajewellery.com/products/" in item["link"]
    assert item["imageLink"] == "https://cdn.shopify.com/s/files/1/dtb-lg-main.jpg"


def test_map_product_no_images():
    """Product with empty images should have empty imageLink."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.shopify_shop_domain = "test.myshopify.com"
        item = map_product_to_merchant_item(_make_product(images=[]))

    assert item is not None
    assert item["imageLink"] == ""


def test_map_product_no_pricing():
    """Product with null pricing should be skipped."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.shopify_shop_domain = "test.myshopify.com"
        assert map_product_to_merchant_item(_make_product(pricing=None)) is None
        assert map_product_to_merchant_item(_make_product(pricing={})) is None


def test_map_product_no_name():
    """Product without name should be skipped."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.shopify_shop_domain = "test.myshopify.com"
        assert map_product_to_merchant_item(_make_product(name="")) is None


def test_get_retail_price_variants():
    """Should extract retail price from first variant."""
    assert _get_retail_price({"default": {"cost": 100, "retail": 250}}) == 250.0
    assert _get_retail_price({}) is None
    assert _get_retail_price(None) is None


# ── Integration: GoogleMerchantSync ──


@pytest.fixture
def merchant_sync():
    """Create GoogleMerchantSync with configured settings."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.google_merchant_id = "5757278712"
        mock_settings.google_service_account_path = ""
        mock_settings.google_service_account_json = '{"type": "service_account"}'
        mock_settings.shopify_shop_domain = "pinaka-jewellery.myshopify.com"
        yield GoogleMerchantSync()


async def test_sync_success(merchant_sync):
    """Successful batch sync should report correct items_synced."""
    products = [_make_product(sku=f"SKU-{i}", name=f"Product {i}") for i in range(3)]

    batch_response = {
        "entries": [
            {"batchId": 0, "product": {"id": "1"}},
            {"batchId": 1, "product": {"id": "2"}},
            {"batchId": 2, "product": {"id": "3"}},
        ]
    }

    with patch.object(merchant_sync, "_execute_batch", return_value=batch_response):
        result = await merchant_sync.sync_products(products)

    assert isinstance(result, MerchantSyncResult)
    assert result.items_synced == 3
    assert result.items_failed == 0
    assert result.status == "ok"


async def test_sync_partial_failure(merchant_sync):
    """Some items failing should report partial_failure."""
    products = [_make_product(sku=f"SKU-{i}", name=f"Product {i}") for i in range(3)]

    batch_response = {
        "entries": [
            {"batchId": 0, "product": {"id": "1"}},
            {"batchId": 1, "errors": {"errors": [{"message": "Invalid image"}]}},
            {"batchId": 2, "product": {"id": "3"}},
        ]
    }

    with patch.object(merchant_sync, "_execute_batch", return_value=batch_response):
        result = await merchant_sync.sync_products(products)

    assert result.items_synced == 2
    assert result.items_failed == 1
    assert result.status == "partial_failure"


async def test_sync_empty_products(merchant_sync):
    """Empty product list should skip."""
    result = await merchant_sync.sync_products([])
    assert result.items_synced == 0
    assert result.status == "skipped"


async def test_sync_not_configured():
    """Unconfigured client should return error."""
    with patch("src.marketing.google_merchant.settings") as mock_settings:
        mock_settings.google_merchant_id = ""
        mock_settings.google_service_account_path = ""
        mock_settings.google_service_account_json = ""
        sync = GoogleMerchantSync()

    result = await sync.sync_products([_make_product()])
    assert result.status != "ok"
    assert len(result.errors) == 1
