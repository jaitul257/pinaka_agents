"""Tests for shipping processor fraud detection and insurance validation."""

from unittest.mock import AsyncMock, patch

from src.shipping.processor import ShippingProcessor


def _make_processor():
    """Create a ShippingProcessor with mocked dependencies."""
    with patch("src.shipping.processor.AsyncDatabase") as mock_db, \
         patch("src.shipping.processor.SlackNotifier"), \
         patch("src.shipping.processor.RateLimitedClient"):
        processor = ShippingProcessor()
        processor._db = AsyncMock()
        processor._db.count_orders_from_email_24h.return_value = 0
        return processor


async def test_fraud_check_clean_order():
    """Normal order should pass fraud check."""
    processor = _make_processor()
    result = await processor.check_fraud({
        "shopify_order_id": 1001,
        "total": 2000.00,
        "buyer_email": "buyer@example.com",
    })
    assert not result.is_flagged
    assert len(result.reasons) == 0


async def test_fraud_check_high_value():
    """Orders over $5K should be flagged."""
    processor = _make_processor()
    result = await processor.check_fraud({
        "shopify_order_id": 1002,
        "total": 9500.00,
        "buyer_email": "buyer@example.com",
    })
    assert result.is_flagged
    assert result.requires_video_verification
    assert any("High value" in r for r in result.reasons)


async def test_fraud_check_velocity():
    """Multiple orders from same buyer in 24h should be flagged."""
    processor = _make_processor()
    processor._db.count_orders_from_email_24h.return_value = 2
    result = await processor.check_fraud({
        "shopify_order_id": 1003,
        "total": 2850.00,
        "buyer_email": "buyer@example.com",
    })
    assert result.is_flagged
    assert any("Velocity" in r for r in result.reasons)


async def test_fraud_check_insurance_gap():
    """Orders exceeding carrier insurance cap should be flagged."""
    processor = _make_processor()
    result = await processor.check_fraud({
        "shopify_order_id": 1004,
        "total": 4000.00,
        "buyer_email": "buyer@example.com",
    })
    assert result.is_flagged
    assert result.insurance_gap == 1500.00
    assert any("Insurance gap" in r for r in result.reasons)


def test_insurance_validation_covered():
    """Orders within carrier cap should be covered."""
    processor = _make_processor()
    result = processor.validate_insurance(2000.00)
    assert result["covered"]
    assert result["gap"] == 0.0


def test_insurance_validation_gap():
    """Orders exceeding carrier cap should show the gap."""
    processor = _make_processor()
    result = processor.validate_insurance(4000.00)
    assert not result["covered"]
    assert result["gap"] == 1500.00
    assert "supplemental" in result["action_required"].lower()
