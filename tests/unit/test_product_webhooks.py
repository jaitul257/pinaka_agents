"""Unit tests for products/create, products/update, products/delete webhook handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.shopify_webhooks import (
    _process_product_create_or_update,
    _process_product_delete,
    _shopify_product_to_supabase_row,
)


# ── Translator ──

def test_translator_handles_full_payload():
    sp = {
        "id": 81112236290,
        "title": "Diamond Tennis Bracelet — Yellow Gold 3CT",
        "handle": "diamond-tennis-bracelet-yellow-gold",
        "product_type": "Bracelet",
        "status": "active",
        "tags": "diamond, tennis, yellow-gold, milestone",
        "body_html": "<p>Handcrafted story.</p>",
        "images": [
            {"src": "https://cdn.shopify.com/img1.jpg"},
            {"src": "https://cdn.shopify.com/img2.jpg"},
        ],
        "variants": [
            {"id": 1001, "sku": "DTB-LBG-7-14YKG", "title": "7 inch",
             "price": "4500.00", "option1": "Yellow Gold", "option2": "7 inch"},
            {"id": 1002, "sku": "DTB-LBG-8-14YKG", "title": "8 inch",
             "price": "4700.00", "option1": "Yellow Gold", "option2": "8 inch"},
        ],
    }
    row = _shopify_product_to_supabase_row(sp)
    assert row["sku"] == "DTB-LBG-7-14YKG"  # first variant's SKU wins
    assert row["name"] == sp["title"]
    assert row["shopify_product_id"] == 81112236290
    assert row["handle"] == "diamond-tennis-bracelet-yellow-gold"
    assert row["status"] == "active"
    assert len(row["images"]) == 2
    assert row["tags"] == ["diamond", "tennis", "yellow-gold", "milestone"]
    assert row["carats"] == "3CT"
    assert len(row["variant_options"]) == 2


def test_translator_handles_missing_sku():
    """Product without variant SKU → row with empty sku (handler will skip)."""
    sp = {"id": 1, "title": "No SKU", "variants": [{"id": 1, "sku": ""}]}
    row = _shopify_product_to_supabase_row(sp)
    assert row["sku"] == ""


def test_translator_defaults_status_to_draft():
    sp = {"id": 1, "title": "T", "variants": [{"sku": "X"}]}
    row = _shopify_product_to_supabase_row(sp)
    assert row["status"] == "draft"


def test_translator_handles_empty_images_and_tags():
    sp = {"id": 1, "title": "T", "variants": [{"sku": "X"}], "images": [], "tags": ""}
    row = _shopify_product_to_supabase_row(sp)
    assert row["images"] == []
    assert row["tags"] == []


# ── Create/Update handler ──

@pytest.mark.asyncio
async def test_create_or_update_upserts_to_supabase():
    sp = {
        "id": 555, "title": "Test", "handle": "test",
        "status": "active", "tags": "",
        "variants": [{"sku": "TEST-SKU", "price": "100", "option1": "Yellow"}],
        "images": [{"src": "https://cdn.shopify.com/x.jpg"}],
    }
    with patch("src.api.shopify_webhooks._get_async_db") as mock_db_factory:
        mock_db = AsyncMock()
        mock_db_factory.return_value = mock_db
        await _process_product_create_or_update(sp)
    mock_db.upsert_product.assert_awaited_once()
    row = mock_db.upsert_product.call_args[0][0]
    assert row["sku"] == "TEST-SKU"
    assert row["shopify_product_id"] == 555


@pytest.mark.asyncio
async def test_create_or_update_skips_missing_sku():
    sp = {"id": 555, "title": "No SKU", "variants": [{"sku": ""}]}
    with patch("src.api.shopify_webhooks._get_async_db") as mock_db_factory:
        mock_db = AsyncMock()
        mock_db_factory.return_value = mock_db
        await _process_product_create_or_update(sp)
    mock_db.upsert_product.assert_not_called()


@pytest.mark.asyncio
async def test_create_or_update_silences_db_errors():
    """DB failure must not crash the webhook request path."""
    sp = {"id": 1, "title": "T", "variants": [{"sku": "X"}]}
    with patch("src.api.shopify_webhooks._get_async_db") as mock_db_factory:
        mock_db = AsyncMock()
        mock_db.upsert_product.side_effect = Exception("DB down")
        mock_db_factory.return_value = mock_db
        # Should not raise
        await _process_product_create_or_update(sp)


# ── Delete handler ──

@pytest.mark.asyncio
async def test_delete_removes_matching_product():
    with patch("src.api.shopify_webhooks._get_async_db") as mock_db_factory:
        mock_db = AsyncMock()
        mock_db.get_product_by_shopify_id.return_value = {"sku": "OLD", "shopify_product_id": 999}
        mock_db_factory.return_value = mock_db
        await _process_product_delete({"id": 999})
    mock_db.delete_product_by_shopify_id.assert_awaited_once_with(999)


@pytest.mark.asyncio
async def test_delete_noop_when_not_in_supabase():
    with patch("src.api.shopify_webhooks._get_async_db") as mock_db_factory:
        mock_db = AsyncMock()
        mock_db.get_product_by_shopify_id.return_value = None
        mock_db_factory.return_value = mock_db
        await _process_product_delete({"id": 999})
    mock_db.delete_product_by_shopify_id.assert_not_called()


@pytest.mark.asyncio
async def test_delete_skips_missing_id():
    with patch("src.api.shopify_webhooks._get_async_db") as mock_db_factory:
        mock_db = AsyncMock()
        mock_db_factory.return_value = mock_db
        await _process_product_delete({})  # no id at all
    mock_db.delete_product_by_shopify_id.assert_not_called()
