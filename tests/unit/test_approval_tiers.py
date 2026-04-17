"""Unit tests for Phase 12 tier classification."""

from src.agents.approval_tiers import Tier, classify


def test_welcome_series_is_auto():
    assert classify("lifecycle_welcome_1") == Tier.AUTO
    assert classify("lifecycle_welcome_5") == Tier.AUTO


def test_crafting_update_is_auto():
    assert classify("crafting_update_email") == Tier.AUTO


def test_customer_response_stays_review():
    assert classify("customer_response") == Tier.REVIEW


def test_unknown_action_defaults_review():
    """Safe fallback — don't auto-send something we haven't classified."""
    assert classify("some_brand_new_thing") == Tier.REVIEW


def test_fraud_review_escalates():
    assert classify("fraud_review") == Tier.ESCALATE
    assert classify("order_cancel") == Tier.ESCALATE
    assert classify("budget_change") == Tier.ESCALATE


def test_tier_values_are_strings():
    """Tier.AUTO == 'auto' so it serializes cleanly to JSON."""
    assert Tier.AUTO.value == "auto"
    assert Tier.REVIEW.value == "review"
    assert Tier.ESCALATE.value == "escalate"
