"""Unit tests for Shopify → Supabase continuous reconciliation."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.shopify_sync import (
    _parse_link_header,
    reconcile_customers,
    reconcile_products,
)


def _mk_httpx_response(status_code: int, json_data: dict, link_header: str = ""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = ""
    resp.json.return_value = json_data
    resp.headers = {"Link": link_header}
    return resp


def _mk_async_client(*responses):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get = AsyncMock(side_effect=list(responses))
    return client


# ── Link header pagination ──

def test_parse_link_header_next():
    h = '<https://shop.myshopify.com/admin/api/2025-01/products.json?page_info=xyz>; rel="next"'
    assert _parse_link_header(h, "next") == \
        "https://shop.myshopify.com/admin/api/2025-01/products.json?page_info=xyz"


def test_parse_link_header_no_next():
    h = '<https://shop.myshopify.com/admin/api/2025-01/products.json?page_info=xyz>; rel="previous"'
    assert _parse_link_header(h, "next") is None


def test_parse_link_header_empty():
    assert _parse_link_header("", "next") is None
    assert _parse_link_header("garbage", "next") is None


# ── reconcile_products ──

@pytest.mark.asyncio
async def test_reconcile_products_no_shopify_config():
    """No shop/token → skipped, not error."""
    with patch("src.core.shopify_sync.settings") as s:
        s.shopify_shop_domain = ""
        s.shopify_access_token = ""
        s.shopify_api_version = "2025-01"
        result = await reconcile_products()
    assert result["skip_reason"] == "shopify_not_configured"
    assert result["upserted"] == 0


@pytest.mark.asyncio
async def test_reconcile_products_happy_path():
    """3 products in Shopify → 3 upserts, 0 deletes."""
    shopify_batch = {
        "products": [
            {"id": 1, "title": "A", "variants": [{"sku": "SKU-A"}], "images": []},
            {"id": 2, "title": "B", "variants": [{"sku": "SKU-B"}], "images": []},
            {"id": 3, "title": "C", "variants": [{"sku": ""}], "images": []},  # skipped
        ]
    }
    resp = _mk_httpx_response(200, shopify_batch)

    mock_db = AsyncMock()
    # get_all_products returns existing rows — all should be kept (all have shopify_id in seen set)
    mock_db.get_all_products.return_value = [
        {"sku": "SKU-A", "shopify_product_id": 1},
        {"sku": "SKU-B", "shopify_product_id": 2},
    ]
    mock_db.upsert_product.return_value = {}

    with patch("src.core.shopify_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(resp)):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_api_version = "2025-01"
        result = await reconcile_products(db=mock_db)

    assert result["upserted"] == 2   # two with valid SKU
    assert result["skipped"] == 1    # third has empty SKU
    assert result["deleted"] == 0    # nothing to delete
    assert mock_db.upsert_product.await_count == 2


@pytest.mark.asyncio
async def test_reconcile_products_deletes_orphans():
    """Supabase row with shopify_product_id not in Shopify list → deleted."""
    shopify_batch = {
        "products": [
            {"id": 1, "title": "A", "variants": [{"sku": "SKU-A"}], "images": []},
        ]
    }
    resp = _mk_httpx_response(200, shopify_batch)

    mock_db = AsyncMock()
    mock_db.get_all_products.return_value = [
        {"sku": "SKU-A", "shopify_product_id": 1},
        {"sku": "SKU-GONE", "shopify_product_id": 999},  # orphan
        {"sku": "SKU-NEVER-SYNCED", "shopify_product_id": None},  # local-only, keep
    ]
    mock_db.upsert_product.return_value = {}

    with patch("src.core.shopify_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(resp)):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_api_version = "2025-01"
        result = await reconcile_products(db=mock_db, delete_missing=True)

    assert result["deleted"] == 1
    mock_db.delete_product_by_shopify_id.assert_awaited_once_with(999)


@pytest.mark.asyncio
async def test_reconcile_products_delete_missing_false():
    """delete_missing=False → never removes orphans."""
    shopify_batch = {"products": [{"id": 1, "title": "A", "variants": [{"sku": "SKU-A"}]}]}
    resp = _mk_httpx_response(200, shopify_batch)

    mock_db = AsyncMock()
    mock_db.get_all_products.return_value = [{"sku": "ORPHAN", "shopify_product_id": 999}]
    mock_db.upsert_product.return_value = {}

    with patch("src.core.shopify_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(resp)):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_api_version = "2025-01"
        result = await reconcile_products(db=mock_db, delete_missing=False)

    assert result["deleted"] == 0
    mock_db.delete_product_by_shopify_id.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_products_paginates():
    """Walks Shopify's Link-header pagination across multiple pages."""
    page1 = _mk_httpx_response(
        200,
        {"products": [{"id": 1, "title": "A", "variants": [{"sku": "SKU-A"}]}]},
        link_header='<https://test.myshopify.com/admin/api/2025-01/products.json?page_info=aaa>; rel="next"',
    )
    page2 = _mk_httpx_response(
        200,
        {"products": [{"id": 2, "title": "B", "variants": [{"sku": "SKU-B"}]}]},
        link_header="",  # no more pages
    )

    mock_db = AsyncMock()
    mock_db.get_all_products.return_value = []
    mock_db.upsert_product.return_value = {}

    with patch("src.core.shopify_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(page1, page2)):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_api_version = "2025-01"
        result = await reconcile_products(db=mock_db, delete_missing=False)

    assert result["shopify_total"] == 2
    assert result["upserted"] == 2


# ── reconcile_customers ──

@pytest.mark.asyncio
async def test_reconcile_customers_upserts():
    resp = _mk_httpx_response(200, {
        "customers": [
            {"id": 1, "email": "a@b.com", "first_name": "A", "last_name": "B",
             "accepts_marketing": True, "orders_count": 2, "total_spent": "9800.00"},
            {"id": 2, "email": "c@d.com", "first_name": "C", "last_name": "D",
             "orders_count": 0, "total_spent": "0"},
        ]
    })

    mock_db = AsyncMock()
    mock_db.upsert_customer.return_value = {}

    with patch("src.core.shopify_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(resp)):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_api_version = "2025-01"
        result = await reconcile_customers(db=mock_db)

    assert result["upserted"] == 2
    calls = [c.args[0] for c in mock_db.upsert_customer.await_args_list]
    assert calls[0]["lifetime_value"] == 9800.0
    assert calls[0]["accepts_marketing"] is True
    assert calls[1]["lifetime_value"] == 0.0


@pytest.mark.asyncio
async def test_reconcile_customers_skips_missing_id():
    resp = _mk_httpx_response(200, {
        "customers": [
            {"id": None, "email": "no-id@x.com"},
        ]
    })
    mock_db = AsyncMock()

    with patch("src.core.shopify_sync.settings") as s, \
         patch("httpx.AsyncClient", return_value=_mk_async_client(resp)):
        s.shopify_shop_domain = "test.myshopify.com"
        s.shopify_access_token = "x"
        s.shopify_api_version = "2025-01"
        result = await reconcile_customers(db=mock_db)

    assert result["skipped"] == 1
    mock_db.upsert_customer.assert_not_called()
