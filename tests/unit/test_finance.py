"""Tests for finance calculator — per-order profit, Shopify fees, and daily summaries."""

from datetime import date
from unittest.mock import patch

from src.finance.calculator import (
    SHOPIFY_PAYMENT_PROCESSING_FIXED,
    SHOPIFY_PAYMENT_PROCESSING_RATE,
    FinanceCalculator,
)


def _make_calculator():
    """Create a FinanceCalculator with mocked dependencies."""
    with patch("src.finance.calculator.AsyncDatabase"), \
         patch("src.finance.calculator.SlackNotifier"):
        return FinanceCalculator()


def test_shopify_fees_basic():
    """Shopify fees should be 2.9% + $0.30 per transaction."""
    calc = _make_calculator()
    fees = calc.calculate_shopify_fees(100.0)

    expected = 100.0 * SHOPIFY_PAYMENT_PROCESSING_RATE + SHOPIFY_PAYMENT_PROCESSING_FIXED
    assert fees == round(expected, 2)


def test_shopify_fees_high_value():
    """Fee calculation should scale linearly with order total."""
    calc = _make_calculator()
    fees_100 = calc.calculate_shopify_fees(100.0)
    fees_1000 = calc.calculate_shopify_fees(1000.0)
    # 10x the order, fees should be roughly 10x (minus fixed component)
    assert fees_1000 > fees_100 * 5


def test_shopify_fees_zero():
    """Zero-value order should only have the fixed fee."""
    calc = _make_calculator()
    fees = calc.calculate_shopify_fees(0.0)
    assert fees == SHOPIFY_PAYMENT_PROCESSING_FIXED


def test_order_profit_positive():
    """Standard jewellery order should have positive profit."""
    calc = _make_calculator()
    profit = calc.calculate_order_profit({
        "shopify_order_id": 1001,
        "total": 2850.0,
        "cogs": 450.0,
        "shipping_cost": 15.0,
        "ad_spend": 5.0,
    })
    assert profit.shopify_order_id == 1001
    assert profit.revenue == 2850.0
    assert profit.cogs == 450.0
    assert profit.net_profit > 0
    assert profit.margin_pct > 0
    # Shopify fees on $2850: 2.9% + $0.30 = $82.95
    assert abs(profit.shopify_fees - 82.95) < 0.01


def test_order_profit_no_offsite_ads():
    """Shopify has no offsite ads fee — only payment processing."""
    calc = _make_calculator()
    profit = calc.calculate_order_profit({
        "shopify_order_id": 1001,
        "total": 2850.0,
        "cogs": 450.0,
        "shipping_cost": 15.0,
    })
    expected_fees = 2850.0 * SHOPIFY_PAYMENT_PROCESSING_RATE + SHOPIFY_PAYMENT_PROCESSING_FIXED
    assert profit.shopify_fees == round(expected_fees, 2)


def test_order_profit_zero_revenue():
    """Zero-revenue order should have zero margin percent."""
    calc = _make_calculator()
    profit = calc.calculate_order_profit({
        "shopify_order_id": 0,
        "total": 0,
        "cogs": 0,
        "shipping_cost": 0,
    })
    assert profit.margin_pct == 0.0


def test_daily_summary_aggregation():
    """Daily summary should correctly aggregate multiple orders."""
    calc = _make_calculator()
    orders = [
        {"shopify_order_id": 1, "total": 2850.0, "cogs": 450.0, "shipping_cost": 15.0, "ad_spend": 5.0},
        {"shopify_order_id": 2, "total": 1200.0, "cogs": 200.0, "shipping_cost": 10.0, "ad_spend": 3.0},
    ]
    summary = calc.summarize_daily(orders, date(2026, 3, 27))
    assert summary.order_count == 2
    assert summary.total_revenue == 2850.0 + 1200.0
    assert summary.total_cogs == 450.0 + 200.0
    assert summary.avg_order_value == round((2850.0 + 1200.0) / 2, 2)
    assert summary.total_net_profit > 0


def test_daily_summary_empty():
    """Empty order list should return zeroed summary."""
    calc = _make_calculator()
    summary = calc.summarize_daily([], date(2026, 3, 27))
    assert summary.order_count == 0
    assert summary.total_revenue == 0
    assert summary.total_net_profit == 0
    assert summary.avg_margin_pct == 0
