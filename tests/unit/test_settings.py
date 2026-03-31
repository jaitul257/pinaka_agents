"""Tests for core settings and configuration."""

from src.core.settings import Settings


def test_default_settings():
    """Settings should load with sensible defaults."""
    s = Settings(_env_file=None)
    assert s.daily_token_budget == 500_000
    assert s.insurance_required_above == 500.0
    assert s.carrier_insurance_cap == 2500.0
    assert s.high_value_threshold == 5000.0
    assert s.roas_window_days == 30
    assert s.made_to_order_days == 15
    assert s.crafting_update_delay_days == 3
    assert s.abandoned_cart_delay_minutes == 60
    assert s.max_cart_recovery_emails_per_week == 2


def test_shopify_admin_url():
    """Shopify admin URL should be constructed from domain and API version."""
    s = Settings(
        _env_file=None,
        shopify_shop_domain="pinaka.myshopify.com",
        shopify_api_version="2025-01",
    )
    assert s.shopify_admin_url == "https://pinaka.myshopify.com/admin/api/2025-01"


def test_shopify_admin_url_empty():
    """Empty domain should still produce a URL (will fail at runtime)."""
    s = Settings(_env_file=None)
    assert s.shopify_admin_url == "https:///admin/api/2026-01"


def test_rate_limits():
    """Rate limits should have sensible defaults for Shopify and SendGrid."""
    s = Settings(_env_file=None)
    assert s.shopify_qps == 2.0
    assert s.sendgrid_qps == 1.0
    assert s.claude_rpm == 50
