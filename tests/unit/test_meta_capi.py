"""Tests for MetaConversionsAPI — CAPI event fires (ViewContent, AddToCart, IC, Purchase)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.marketing.meta_capi import (
    MetaConversionsAPI,
    _build_user_data,
    _normalize_and_hash,
    _normalize_phone,
)


def _mock_response(status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = ""
    return resp


def _mock_async_client(response):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=response)
    return mock_client


@pytest.fixture
def configured_api():
    """CAPI with pixel + token set."""
    with patch("src.marketing.meta_capi.settings") as mock_settings:
        mock_settings.meta_pixel_id = "1422946742915513"
        mock_settings.meta_capi_access_token = "test-capi-token"
        mock_settings.meta_graph_api_version = "v25.0"
        mock_settings.shopify_shop_domain = "pinaka-jewellery.myshopify.com"
        yield MetaConversionsAPI()


def test_unconfigured_api_returns_false():
    with patch("src.marketing.meta_capi.settings") as mock_settings:
        mock_settings.meta_pixel_id = ""
        mock_settings.meta_capi_access_token = ""
        api = MetaConversionsAPI()
        assert not api.is_configured


def test_normalize_and_hash_lowercases_and_trims():
    h1 = _normalize_and_hash("  Foo@Bar.Com ")
    h2 = _normalize_and_hash("foo@bar.com")
    assert h1 == h2 and len(h1) == 64


def test_normalize_phone_adds_us_country_code():
    # 10-digit US number gets "1" prefix before hashing
    ten = _normalize_phone("(617) 555-0100")
    eleven = _normalize_phone("16175550100")
    assert ten == eleven


def test_build_user_data_skips_empty_fields():
    data = _build_user_data(email="a@b.com")
    assert "em" in data
    assert "ph" not in data and "fn" not in data and "ln" not in data


def test_build_user_data_includes_fbp_fbc_raw():
    """fbp/fbc ship raw (not hashed)."""
    data = _build_user_data(fbp="fb.1.123.456", fbc="fb.1.789.click_id")
    assert data["fbp"] == "fb.1.123.456"
    assert data["fbc"] == "fb.1.789.click_id"


@pytest.mark.asyncio
async def test_send_view_content_builds_correct_event(configured_api):
    captured = {}

    def capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_response(200)

    mock_client = _mock_async_client(_mock_response(200))
    mock_client.post = AsyncMock(side_effect=capture)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await configured_api.send_view_content(
            product_id="prod_123", value=4900.0, event_id="vc_abc",
            customer_email="test@example.com", fbp="fb.1.x",
            source_url="https://pinakajewellery.com/products/diamond-tennis-bracelet",
        )

    assert ok is True
    event = captured["json"]["data"][0]
    assert event["event_name"] == "ViewContent"
    assert event["event_id"] == "vc_abc"
    assert event["custom_data"]["content_ids"] == ["prod_123"]
    assert event["custom_data"]["value"] == 4900.0
    assert event["custom_data"]["content_type"] == "product"
    assert event["event_source_url"] == "https://pinakajewellery.com/products/diamond-tennis-bracelet"
    assert event["user_data"]["fbp"] == "fb.1.x"


@pytest.mark.asyncio
async def test_send_add_to_cart_includes_num_items(configured_api):
    captured = {}
    def capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_response(200)

    mock_client = _mock_async_client(_mock_response(200))
    mock_client.post = AsyncMock(side_effect=capture)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await configured_api.send_add_to_cart(
            product_id="prod_777", value=5100.0, event_id="atc_xyz", quantity=2,
        )
    assert ok is True
    event = captured["json"]["data"][0]
    assert event["event_name"] == "AddToCart"
    assert event["custom_data"]["num_items"] == 2


@pytest.mark.asyncio
async def test_send_initiate_checkout_multiple_products(configured_api):
    captured = {}
    def capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_response(200)

    mock_client = _mock_async_client(_mock_response(200))
    mock_client.post = AsyncMock(side_effect=capture)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await configured_api.send_initiate_checkout(
            content_ids=["a", "b", "c"], value=15000.0, event_id="ic_42",
        )
    assert ok is True
    event = captured["json"]["data"][0]
    assert event["event_name"] == "InitiateCheckout"
    assert event["custom_data"]["content_ids"] == ["a", "b", "c"]
    assert event["custom_data"]["num_items"] == 3


@pytest.mark.asyncio
async def test_send_purchase_event_uses_order_id_for_dedup(configured_api):
    captured = {}
    def capture(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_response(200)

    mock_client = _mock_async_client(_mock_response(200))
    mock_client.post = AsyncMock(side_effect=capture)

    order_data = {
        "id": 5001,
        "total_price": "4900.00",
        "currency": "USD",
        "line_items": [
            {"product_id": 111, "quantity": 1},
            {"product_id": 222, "quantity": 2},
        ],
        "order_number": 1042,
    }

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await configured_api.send_purchase_event(
            order_data=order_data, customer_email="buyer@example.com",
            fbp="fb.1.abc", fbc="fb.1.click",
        )
    assert ok is True
    event = captured["json"]["data"][0]
    assert event["event_name"] == "Purchase"
    assert event["event_id"] == "5001"  # For dedup with browser pixel
    assert event["custom_data"]["order_id"] == "1042"
    assert event["custom_data"]["content_ids"] == ["111", "222"]
    assert event["custom_data"]["num_items"] == 3
    assert event["user_data"]["fbp"] == "fb.1.abc"


@pytest.mark.asyncio
async def test_api_500_returns_false_not_raises(configured_api):
    resp = _mock_response(500)
    resp.text = "Internal Server Error"
    mock_client = _mock_async_client(resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await configured_api.send_view_content(
            product_id="p", value=0, event_id="x",
        )
    assert ok is False


@pytest.mark.asyncio
async def test_network_error_returns_false_not_raises(configured_api):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("boom"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await configured_api.send_add_to_cart(
            product_id="p", value=0, event_id="x",
        )
    assert ok is False
