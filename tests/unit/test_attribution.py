"""Tests for attribution parameter extraction from Shopify landing_site URLs."""

from src.core.attribution import extract_attribution


def test_extract_gclid_from_landing_site():
    """Should extract gclid and utm params from landing_site query string."""
    order = {"landing_site": "https://shop.com/products?gclid=abc123&utm_source=google"}
    result = extract_attribution(order)
    assert result["gclid"] == "abc123"
    assert result["utm_source"] == "google"
    assert result["fbclid"] is None
    assert result["utm_medium"] is None
    assert result["utm_campaign"] is None


def test_extract_fbclid_from_landing_site():
    """Should extract fbclid from landing_site."""
    order = {"landing_site": "https://shop.com?fbclid=xyz789"}
    result = extract_attribution(order)
    assert result["fbclid"] == "xyz789"
    assert result["gclid"] is None


def test_extract_no_params():
    """URL without query params should return all None."""
    order = {"landing_site": "https://shop.com/products/ring"}
    result = extract_attribution(order)
    assert all(v is None for v in result.values())


def test_extract_no_landing_site():
    """Missing landing_site should return all None."""
    result = extract_attribution({})
    assert all(v is None for v in result.values())


def test_extract_none_landing_site():
    """Explicit None landing_site should return all None."""
    result = extract_attribution({"landing_site": None})
    assert all(v is None for v in result.values())


def test_extract_malformed_url():
    """Malformed URL should not crash, return all None."""
    order = {"landing_site": "not-a-url"}
    result = extract_attribution(order)
    # parse_qs handles this gracefully (no query = no params)
    assert all(v is None for v in result.values())


def test_extract_partial_utm():
    """Should extract only the UTM params that are present."""
    order = {
        "landing_site": "https://shop.com?utm_source=instagram&utm_campaign=spring_sale"
    }
    result = extract_attribution(order)
    assert result["utm_source"] == "instagram"
    assert result["utm_campaign"] == "spring_sale"
    assert result["utm_medium"] is None
    assert result["gclid"] is None
    assert result["fbclid"] is None


def test_extract_all_params():
    """Should handle all attribution params present simultaneously."""
    order = {
        "landing_site": (
            "https://shop.com/collections/rings"
            "?gclid=g123&fbclid=f456&utm_source=google"
            "&utm_medium=cpc&utm_campaign=jewelry"
        )
    }
    result = extract_attribution(order)
    assert result["gclid"] == "g123"
    assert result["fbclid"] == "f456"
    assert result["utm_source"] == "google"
    assert result["utm_medium"] == "cpc"
    assert result["utm_campaign"] == "jewelry"
