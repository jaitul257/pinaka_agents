"""Integration tests for dashboard product edit and per-size pricing flow.

Verifies that:
1. Price values from the form reach _build_shopify_variants correctly
2. Size values with " quotes survive HTML round-trip
3. Shopify API receives the correct prices in variant payloads
4. Edit-by-Shopify-ID route loads and saves product correctly
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.dashboard.web import _build_shopify_variants, _shopify_product_to_local


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_cookie():
    with patch("src.dashboard.web._check_auth", return_value=True):
        yield {"dash_token": "test-token"}


# ── Unit: _build_shopify_variants price mapping ──


class TestBuildShopifyVariants:
    def test_prices_applied_to_all_metals(self):
        """Each metal variant gets the correct per-size price."""
        size_prices = {'6"': 8998.0, '6.5"': 9498.0, '7"': 9998.0, '7.5"': 10498.0}
        variants = _build_shopify_variants(
            "DTB-LBG",
            ["Yellow Gold", "White Gold", "Rose Gold"],
            ['6"', '6.5"', '7"', '7.5"'],
            size_prices,
        )
        assert len(variants) == 12

        # Check prices by size
        for v in variants:
            expected_price = size_prices[v["option2"]]
            assert v["price"] == str(expected_price), (
                f'{v["option1"]} / {v["option2"]}: expected ${expected_price}, got ${v["price"]}'
            )

    def test_sku_format(self):
        """Variant SKUs follow base-MC-SC pattern."""
        variants = _build_shopify_variants(
            "DTB-LBG",
            ["Yellow Gold"],
            ['7"'],
            {'7"': 9998.0},
        )
        assert len(variants) == 1
        assert variants[0]["sku"] == "DTB-LBG-YG-7"
        assert variants[0]["price"] == "9998.0"

    def test_missing_price_defaults_to_zero(self):
        """Sizes not in size_prices default to 0."""
        variants = _build_shopify_variants(
            "DTB-LBG",
            ["Rose Gold"],
            ['7.5"'],
            {},  # empty price dict
        )
        assert variants[0]["price"] == "0"

    def test_size_without_quote_misses(self):
        """Demonstrates the bug: sizes without quotes don't match quoted keys."""
        size_prices = {'6"': 8998.0}
        # Size WITHOUT quote — this is what the old broken form sent
        variants = _build_shopify_variants("DTB", ["Yellow Gold"], ["6"], size_prices)
        # "6" (no quote) doesn't match '6"' (with quote) — price is 0
        assert variants[0]["price"] == "0"

    def test_size_with_quote_matches(self):
        """After fix: sizes with quotes match quoted keys correctly."""
        size_prices = {'6"': 8998.0}
        # Size WITH quote — this is what the fixed form sends
        variants = _build_shopify_variants("DTB", ["Yellow Gold"], ['6"'], size_prices)
        assert variants[0]["price"] == "8998.0"


# ── Unit: _shopify_product_to_local ──


class TestShopifyProductToLocal:
    def test_derives_base_sku(self):
        """Strips variant suffix from first variant SKU to get base SKU."""
        shopify_product = {
            "id": 12345,
            "title": "Test Bracelet",
            "product_type": "Bracelets",
            "body_html": "<p>A story</p>",
            "tags": "tag1, tag2",
            "variants": [
                {"sku": "DTB-LBG-YG-6", "option1": "Yellow Gold", "option2": '6"', "price": "9998.00"},
                {"sku": "DTB-LBG-WG-7", "option1": "White Gold", "option2": '7"', "price": "10998.00"},
            ],
        }
        result = _shopify_product_to_local(shopify_product)
        assert result["sku"] == "DTB-LBG"  # Not DTB-LBG-YG-6
        assert result["shopify_product_id"] == 12345

    def test_normalizes_sizes_with_quotes(self):
        """Sizes without quotes get normalized to include "."""
        shopify_product = {
            "id": 99,
            "title": "Test",
            "product_type": "",
            "body_html": "",
            "tags": "",
            "variants": [
                {"sku": "X-YG-6", "option1": "Yellow Gold", "option2": "6", "price": "100"},
                {"sku": "X-YG-7", "option1": "Yellow Gold", "option2": "7", "price": "200"},
            ],
        }
        result = _shopify_product_to_local(shopify_product)
        # Sizes should be normalized with "
        assert '6"' in result["variant_options"]["sizes"]
        assert '7"' in result["variant_options"]["sizes"]
        # Prices should be keyed with "
        assert result["variant_options"]["size_pricing"]['6"'] == 100.0
        assert result["variant_options"]["size_pricing"]['7"'] == 200.0

    def test_extracts_metals(self):
        shopify_product = {
            "id": 1,
            "title": "Test",
            "product_type": "",
            "body_html": "",
            "tags": "",
            "variants": [
                {"sku": "X-YG-6", "option1": "Yellow Gold", "option2": '6"', "price": "100"},
                {"sku": "X-RG-6", "option1": "Rose Gold", "option2": '6"', "price": "100"},
            ],
        }
        result = _shopify_product_to_local(shopify_product)
        assert "Rose Gold" in result["variant_options"]["metals"]
        assert "Yellow Gold" in result["variant_options"]["metals"]


# ── Integration: Edit form submit pushes correct prices to Shopify ──


class TestEditProductPricing:
    def test_edit_sends_prices_to_shopify(self, client, auth_cookie):
        """Full round-trip: form submit → price parsing → Shopify API call with correct prices."""
        mock_db = MagicMock()
        mock_db.get_product_by_sku.return_value = {
            "sku": "DTB-LBG",
            "name": "Diamond Tennis Bracelet",
            "shopify_product_id": 9347404857602,
            "category": "Bracelets",
            "materials": {"metal": "Yellow Gold", "total_carat": 3.0, "weight_grams": 12.5, "diamond_type": []},
            "pricing": {},
            "variant_options": {},
            "story": "Test",
            "care_instructions": "",
            "occasions": [],
            "tags": [],
        }
        mock_db.upsert_product.return_value = mock_db.get_product_by_sku.return_value

        captured_payload = {}

        class MockResponse:
            status_code = 200
            def json(self):
                return {"product": {"id": 9347404857602}}

        async def mock_put(url, headers=None, json=None):
            captured_payload.update(json or {})
            return MockResponse()

        mock_http_client = AsyncMock()
        mock_http_client.put = mock_put
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.dashboard.web._get_db", return_value=mock_db), \
             patch("src.dashboard.web.httpx.AsyncClient", return_value=mock_http_client), \
             patch("src.dashboard.web._upsert_google_metafields", new_callable=AsyncMock):

            response = client.post(
                "/dashboard/edit/DTB-LBG",
                data={
                    "name": "Diamond Tennis Bracelet - Lab Grown",
                    "sku": "DTB-LBG",
                    "category": "Bracelets",
                    "metal": "14K Yellow Gold",
                    "total_carat": "3.0",
                    "diamond_type": "lab-grown, VS1-VS2",
                    "weight_grams": "12.5",
                    "variant_name": "DTB-LBG",
                    "cost": "450",
                    "story": "A test story",
                    "care": "Clean gently",
                    "occasions": "anniversary",
                    "tags": "tennis-bracelet",
                    "cert_lab": "None",
                    "cert_number": "",
                    "cert_carat": "0",
                    "cert_clarity": "",
                    "cert_color": "",
                    "embed": "",
                    "push_shopify": "1",
                    # Metal checkboxes
                    "variant_metals": ["Yellow Gold", "White Gold", "Rose Gold"],
                    # Size checkboxes — with " quote (as the fixed form sends)
                    "variant_sizes": ['6"', '6.5"', '7"', '7.5"'],
                    # Per-size prices
                    "price_6": "8998",
                    "price_6_5": "9498",
                    "price_7": "9998",
                    "price_7_5": "10498",
                },
                cookies=auth_cookie,
                follow_redirects=False,
            )

            assert response.status_code == 303
            assert "updated" in response.headers.get("location", "").lower()

            # Verify the Shopify payload has correct prices
            variants = captured_payload.get("product", {}).get("variants", [])
            assert len(variants) == 12, f"Expected 12 variants, got {len(variants)}"

            # Build a lookup: (metal, size) -> price
            price_map = {(v["option1"], v["option2"]): v["price"] for v in variants}

            # All 6" variants should be $8998
            assert price_map[("Yellow Gold", '6"')] == "8998.0"
            assert price_map[("White Gold", '6"')] == "8998.0"
            assert price_map[("Rose Gold", '6"')] == "8998.0"

            # All 7" variants should be $9998
            assert price_map[("Yellow Gold", '7"')] == "9998.0"

            # All 7.5" variants should be $10498
            assert price_map[("Rose Gold", '7.5"')] == "10498.0"

    def test_edit_without_shopify_link(self, client, auth_cookie):
        """Product without shopify_product_id shows 'not linked' message."""
        mock_db = MagicMock()
        mock_db.get_product_by_sku.return_value = {
            "sku": "TEST-SKU",
            "name": "Test Product",
            "shopify_product_id": None,  # Not linked
            "category": "Bracelets",
            "materials": {},
            "pricing": {},
            "story": "",
            "care_instructions": "",
            "occasions": [],
            "tags": [],
        }
        mock_db.upsert_product.return_value = {}

        with patch("src.dashboard.web._get_db", return_value=mock_db):
            response = client.post(
                "/dashboard/edit/TEST-SKU",
                data={
                    "name": "Test",
                    "category": "Bracelets",
                    "metal": "14K Yellow Gold",
                    "total_carat": "3.0",
                    "diamond_type": "lab-grown",
                    "weight_grams": "12.5",
                    "variant_name": "default",
                    "cost": "0",
                    "story": "test",
                    "care": "",
                    "occasions": "",
                    "tags": "",
                    "cert_lab": "None",
                    "cert_number": "",
                    "cert_carat": "0",
                    "cert_clarity": "",
                    "cert_color": "",
                    "embed": "",
                    "push_shopify": "1",
                },
                cookies=auth_cookie,
                follow_redirects=False,
            )

            assert response.status_code == 303
            assert "not+linked" in response.headers.get("location", "")
