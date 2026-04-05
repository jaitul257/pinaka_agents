"""Tests for MetaCreativeClient (Phase 6.1)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.marketing.ad_generator import AdVariant
from src.marketing.meta_creative import (
    MetaCreativeClient,
    MetaCreativeError,
    MetaCreativeResult,
    _slugify,
)


@pytest.fixture
def sample_variant():
    return AdVariant(
        variant_label="A",
        headline="Handcrafted in 14K",
        primary_text="Lab-grown diamonds. Ships in 15 business days.",
        description="Free Lifetime Care",
        cta="SHOP_NOW",
        image_url="https://cdn.shopify.com/a.jpg",
    )


@pytest.fixture
def configured_client():
    """Client with all required settings set via patch."""
    with patch("src.marketing.meta_creative.settings") as mock_settings:
        mock_settings.meta_ads_access_token = "test-token"
        mock_settings.meta_capi_access_token = ""
        mock_settings.meta_ad_account_id = "act_27080581041558231"
        mock_settings.meta_facebook_page_id = "1234567890"
        mock_settings.meta_graph_api_version = "v21.0"
        mock_settings.storefront_domain = "pinakajewellery.com"
        yield MetaCreativeClient()


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = ""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or str(json_data or {})
    return resp


def _mock_async_client(response):
    """Build a mock httpx.AsyncClient that yields `response` from post()."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=response)
    return mock_client


# ── _slugify ──


def test_slugify():
    assert _slugify("Diamond Tennis Bracelet - Lab Grown") == "diamond-tennis-bracelet-lab-grown"
    assert _slugify("14K Gold Ring (Size 7)") == "14k-gold-ring-size-7"
    assert _slugify("") == "product"


# ── Payload shape ──


def test_build_payload_has_all_required_fields(configured_client, sample_variant):
    payload = configured_client._build_payload(
        sample_variant, "Diamond Tennis Bracelet", "DTB-LG-7", "batch-uuid-1234"
    )
    assert payload["status"] == "PAUSED"
    assert "Pinaka" in payload["name"]
    assert "Variant A" in payload["name"]
    assert payload["object_story_spec"]["page_id"] == "1234567890"

    link_data = payload["object_story_spec"]["link_data"]
    assert link_data["message"] == sample_variant.primary_text
    assert link_data["name"] == sample_variant.headline
    assert link_data["picture"] == sample_variant.image_url
    assert link_data["call_to_action"]["type"] == "SHOP_NOW"
    assert "pinakajewellery.com/products/diamond-tennis-bracelet" in link_data["link"]


def test_build_payload_clamps_long_name(configured_client, sample_variant):
    """Payload `name` field has a hard 200-char cap."""
    long_product_name = "X" * 300
    payload = configured_client._build_payload(sample_variant, long_product_name, "SKU", "b")
    assert len(payload["name"]) <= 200


# ── is_configured / _require_configured ──


def test_require_configured_raises_on_missing_token():
    with patch("src.marketing.meta_creative.settings") as s:
        s.meta_ads_access_token = ""
        s.meta_capi_access_token = ""
        s.meta_ad_account_id = "act_1"
        s.meta_facebook_page_id = "1"
        s.meta_graph_api_version = "v21.0"
        client = MetaCreativeClient()
        with pytest.raises(MetaCreativeError, match="ACCESS_TOKEN missing"):
            client._require_configured()


def test_require_configured_raises_on_missing_page_id():
    with patch("src.marketing.meta_creative.settings") as s:
        s.meta_ads_access_token = "t"
        s.meta_capi_access_token = ""
        s.meta_ad_account_id = "act_1"
        s.meta_facebook_page_id = ""
        s.meta_graph_api_version = "v21.0"
        client = MetaCreativeClient()
        with pytest.raises(MetaCreativeError, match="FACEBOOK_PAGE_ID missing"):
            client._require_configured()


# ── create_creative: happy path + error modes ──


async def test_create_creative_200_returns_id(configured_client, sample_variant):
    response = _mock_response(200, {"id": "creative_123456"})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        result = await configured_client.create_creative(
            sample_variant, "Diamond Tennis Bracelet", "DTB-LG-7", "batch-uuid-1234"
        )
    assert isinstance(result, MetaCreativeResult)
    assert result.creative_id == "creative_123456"
    assert result.status == "PAUSED"  # Default


async def test_create_creative_200_with_embedded_error_raises(configured_client, sample_variant):
    """Meta sometimes returns 200 with an error envelope — detect and raise."""
    response = _mock_response(
        200,
        {"error": {"message": "Invalid page_id", "type": "OAuthException", "code": 100}},
    )
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="embedded error"):
            await configured_client.create_creative(
                sample_variant, "Test", "SKU", "batch"
            )


async def test_create_creative_200_without_id_raises(configured_client, sample_variant):
    response = _mock_response(200, {"other_field": "value"})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="no 'id'"):
            await configured_client.create_creative(
                sample_variant, "Test", "SKU", "batch"
            )


async def test_create_creative_400_raises_with_meta_message(configured_client, sample_variant):
    response = _mock_response(
        400,
        {"error": {"message": "Image URL is not accessible", "code": 1487}},
    )
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="Image URL is not accessible"):
            await configured_client.create_creative(
                sample_variant, "Test", "SKU", "batch"
            )


async def test_create_creative_401_raises_auth(configured_client, sample_variant):
    response = _mock_response(401, {"error": {"message": "invalid token"}})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="auth failed"):
            await configured_client.create_creative(
                sample_variant, "Test", "SKU", "batch"
            )


async def test_create_creative_500_raises_with_retry_hint(configured_client, sample_variant):
    response = _mock_response(500, {"error": {"message": "internal"}})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="retry may help"):
            await configured_client.create_creative(
                sample_variant, "Test", "SKU", "batch"
            )


async def test_create_creative_429_raises(configured_client, sample_variant):
    response = _mock_response(429, {})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="rate limited"):
            await configured_client.create_creative(
                sample_variant, "Test", "SKU", "batch"
            )


# ── set_creative_status ──


async def test_set_creative_status_to_active(configured_client):
    response = _mock_response(200, {"success": True})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        result = await configured_client.set_creative_status("creative_123", "ACTIVE")
    assert result.creative_id == "creative_123"
    assert result.status == "ACTIVE"


async def test_set_creative_status_invalid_value_raises(configured_client):
    with pytest.raises(MetaCreativeError, match="Invalid Meta creative update status"):
        await configured_client.set_creative_status("creative_123", "NUKE")


async def test_set_creative_status_paused_raises(configured_client):
    """Meta does not allow flipping ACTIVE → PAUSED on update. Enforce client-side."""
    with pytest.raises(MetaCreativeError, match="PAUSED is create-only"):
        await configured_client.set_creative_status("creative_123", "PAUSED")


async def test_set_creative_status_meta_error(configured_client):
    response = _mock_response(
        400, {"error": {"message": "Creative not found"}}
    )
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="Creative not found"):
            await configured_client.set_creative_status("bad_id", "ACTIVE")
