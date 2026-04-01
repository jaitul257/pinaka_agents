"""Tests for marketing ROAS calculator and budget recommendations."""

from unittest.mock import patch

from src.marketing.ads import AdsTracker


def _make_tracker():
    """Create an AdsTracker with mocked dependencies."""
    with patch("src.marketing.ads.AsyncDatabase"), \
         patch("src.marketing.ads.SlackNotifier"):
        return AdsTracker()


def test_roas_high_performance():
    """ROAS above increase threshold should recommend budget increase."""
    tracker = _make_tracker()
    stats = [
        {"ad_spend_google": 5.0, "ad_spend_meta": 5.0, "revenue": 50.0},
        {"ad_spend_google": 10.0, "ad_spend_meta": 5.0, "revenue": 80.0},
    ]
    result = tracker.calculate_roas(stats, window_days=7)
    assert result.roas == 5.2  # 130/25
    assert result.recommendation == "increase"
    assert result.recommended_budget > result.current_budget


def test_roas_moderate_performance():
    """ROAS between maintain and increase thresholds should maintain budget."""
    tracker = _make_tracker()
    stats = [
        {"ad_spend_google": 10.0, "ad_spend_meta": 10.0, "revenue": 60.0},
    ]
    result = tracker.calculate_roas(stats, window_days=7)
    assert result.roas == 3.0
    assert result.recommendation == "maintain"
    assert result.recommended_budget == result.current_budget


def test_roas_low_performance():
    """ROAS below maintain threshold should decrease budget."""
    tracker = _make_tracker()
    stats = [
        {"ad_spend_google": 15.0, "ad_spend_meta": 10.0, "revenue": 30.0},
    ]
    result = tracker.calculate_roas(stats, window_days=7)
    assert result.roas == 1.2
    assert result.recommendation == "decrease"
    assert result.recommended_budget < result.current_budget
    assert result.recommended_budget >= 5.0  # Floor at $5


def test_roas_zero_spend():
    """Zero ad spend should recommend pause."""
    tracker = _make_tracker()
    stats = [
        {"ad_spend_google": 0, "ad_spend_meta": 0, "revenue": 0},
    ]
    result = tracker.calculate_roas(stats, window_days=7)
    assert result.roas == 0.0
    assert result.recommendation == "pause"
    assert result.recommended_budget == 0.0


def test_roas_empty_stats():
    """Empty stats list should return zero ROAS and pause."""
    tracker = _make_tracker()
    result = tracker.calculate_roas([], window_days=7)
    assert result.roas == 0.0
    assert result.recommendation == "pause"
