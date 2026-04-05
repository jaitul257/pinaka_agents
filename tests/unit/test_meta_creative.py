"""Tests for MetaCreativeClient (Phase 6.1)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.marketing.ad_generator import AdVariant
from src.marketing.meta_creative import (
    MetaAdResult,
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
        mock_settings.meta_default_adset_id = "120244523287540359"
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


# ── create_ad (Phase 6.2) ──


async def test_create_ad_happy_path_uses_default_adset(configured_client):
    """Ad creation reads META_DEFAULT_ADSET_ID from settings when adset_id not passed."""
    response = _mock_response(200, {"id": "120244523999999999"})
    mock_client = _mock_async_client(response)
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient", return_value=mock_client
    ):
        result = await configured_client.create_ad(
            creative_id="959138700395572",
            ad_name="Pinaka — DTB-LBG-7-14YKG — Variant A",
        )
    assert isinstance(result, MetaAdResult)
    assert result.ad_id == "120244523999999999"
    assert result.adset_id == "120244523287540359"  # from fixture settings
    assert result.creative_id == "959138700395572"
    assert result.status == "ACTIVE"  # default

    # Verify the payload sent to Meta
    call_args = mock_client.post.call_args
    payload = call_args.kwargs["data"]
    assert payload["adset_id"] == "120244523287540359"
    assert payload["status"] == "ACTIVE"
    assert '"creative_id": "959138700395572"' in payload["creative"]


async def test_create_ad_with_explicit_adset_overrides_default(configured_client):
    response = _mock_response(200, {"id": "ad_override"})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        result = await configured_client.create_ad(
            creative_id="creative_1",
            ad_name="Override test",
            adset_id="custom_adset",
        )
    assert result.adset_id == "custom_adset"


async def test_create_ad_paused_status(configured_client):
    response = _mock_response(200, {"id": "ad_paused"})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        result = await configured_client.create_ad(
            creative_id="c1", ad_name="Paused ad", status="PAUSED"
        )
    assert result.status == "PAUSED"


async def test_create_ad_invalid_status_raises(configured_client):
    with pytest.raises(MetaCreativeError, match="Must be ACTIVE or PAUSED"):
        await configured_client.create_ad(
            creative_id="c1", ad_name="Bad status", status="ARCHIVED"
        )


async def test_create_ad_missing_default_adset_raises():
    """If neither explicit adset_id nor META_DEFAULT_ADSET_ID is set, raise with setup hint."""
    with patch("src.marketing.meta_creative.settings") as s:
        s.meta_ads_access_token = "t"
        s.meta_capi_access_token = ""
        s.meta_ad_account_id = "act_1"
        s.meta_facebook_page_id = "page_1"
        s.meta_graph_api_version = "v25.0"
        s.meta_default_adset_id = ""
        client = MetaCreativeClient()
        with pytest.raises(MetaCreativeError, match="META_DEFAULT_ADSET_ID"):
            await client.create_ad(creative_id="c1", ad_name="no adset")


async def test_create_ad_400_raises_with_meta_message(configured_client):
    response = _mock_response(
        400, {"error": {"message": "Ad set not found", "code": 100}}
    )
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="Ad set not found"):
            await configured_client.create_ad(creative_id="c1", ad_name="test")


async def test_create_ad_200_with_embedded_error_raises(configured_client):
    response = _mock_response(
        200, {"error": {"message": "creative missing", "code": 100}}
    )
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="embedded error"):
            await configured_client.create_ad(creative_id="c1", ad_name="test")


async def test_create_ad_200_without_id_raises(configured_client):
    response = _mock_response(200, {"other": "value"})
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="no 'id'"):
            await configured_client.create_ad(creative_id="c1", ad_name="test")


async def test_create_ad_name_clamped_to_255(configured_client):
    response = _mock_response(200, {"id": "ad_1"})
    mock_client = _mock_async_client(response)
    long_name = "X" * 500
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient", return_value=mock_client
    ):
        await configured_client.create_ad(creative_id="c1", ad_name=long_name)
    payload = mock_client.post.call_args.kwargs["data"]
    assert len(payload["name"]) <= 255


async def test_create_ad_surfaces_error_user_title_and_msg(configured_client):
    """Real Meta errors put the actionable detail in error_user_title + error_user_msg,
    not in the generic `message` field. Regression test for the "No Payment Method"
    bug where the dashboard showed 'Invalid parameter' instead of 'Update payment method'.
    """
    response = _mock_response(400, {
        "error": {
            "message": "Invalid parameter",  # useless generic
            "error_user_title": "No Payment Method",
            "error_user_msg": "Update payment method: Visit the Billing and payment center.",
            "code": 100,
        }
    })
    with patch(
        "src.marketing.meta_creative.httpx.AsyncClient",
        return_value=_mock_async_client(response),
    ):
        with pytest.raises(MetaCreativeError, match="No Payment Method.*Update payment method"):
            await configured_client.create_ad(creative_id="c1", ad_name="test")
