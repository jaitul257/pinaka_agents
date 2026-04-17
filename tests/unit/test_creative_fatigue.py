"""Tests for creative fatigue detection rules."""

from datetime import date, timedelta

import pytest

from src.marketing.creative_fatigue import detect_fatigue


TODAY = date(2026, 4, 16)


def _row(
    d: date,
    ad_id: str = "ad_1",
    creative_id: str = "cr_1",
    ad_name: str = "Test Ad",
    impressions: int = 0,
    reach: int = 0,
    clicks: int = 0,
    spend: float = 0.0,
    purchase_count: int = 0,
    purchase_value: float = 0.0,
    atc: int = 0,
) -> dict:
    return {
        "date": d.isoformat(),
        "meta_ad_id": ad_id,
        "meta_creative_id": creative_id,
        "ad_name": ad_name,
        "creative_name": "",
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "spend": spend,
        "purchase_count": purchase_count,
        "purchase_value": purchase_value,
        "atc_count": atc,
    }


def _week(start: date, per_day: dict, **overrides) -> list[dict]:
    """Generate 7 days of rows with `per_day` metrics each. Overrides apply uniformly."""
    merged = {**per_day, **overrides}
    return [_row(d=start + timedelta(days=i), **merged) for i in range(7)]


def test_empty_input_no_flags():
    assert detect_fatigue([], TODAY) == []


def test_ignores_ad_with_zero_impressions_this_week():
    # Last week had traffic, this week has zero — we skip rather than flag
    last_week_start = TODAY - timedelta(days=13)
    rows = _week(last_week_start, {"impressions": 1000, "clicks": 20})
    assert detect_fatigue(rows, TODAY) == []


def test_dead_spend_flagged():
    """Spent $100 across 10K impressions, 0 purchases this week → dead_spend flag."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 1500, "reach": 500, "clicks": 25, "spend": 15.0,
    })
    flags = detect_fatigue(rows, TODAY)
    assert len(flags) == 1
    assert flags[0].reason == "dead_spend"
    assert flags[0].meta_ad_id == "ad_1"
    assert "$105" in flags[0].detail or "$15" in flags[0].detail  # whichever format aggregates


def test_dead_spend_below_min_spend_not_flagged():
    """Spent only $10 — below $50 floor, not flagged as dead spend."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 1500, "reach": 500, "clicks": 25, "spend": 1.0,
    })
    # $1 * 7 = $7 — no flag
    flags = detect_fatigue(rows, TODAY)
    assert flags == []


def test_dead_spend_with_purchase_not_flagged():
    """Even with high spend + impressions, a single purchase saves it."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 2000, "reach": 500, "clicks": 30, "spend": 20.0,
        "purchase_count": 0,
    })
    # Force one purchase on one day
    rows[-1]["purchase_count"] = 1
    flags = detect_fatigue(rows, TODAY)
    assert all(f.reason != "dead_spend" for f in flags)


def test_high_frequency_flagged():
    """Impressions 4x reach → frequency = 4.0 → high_freq flag."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 2000, "reach": 500, "clicks": 100, "spend": 5.0,
        "purchase_count": 1,  # avoid dead_spend
    })
    # Total week: impressions=14000, reach stays 500 → freq = 28 — way above threshold
    # Actually _aggregate uses max reach across days (approximation).
    flags = detect_fatigue(rows, TODAY)
    assert len(flags) == 1
    assert flags[0].reason == "high_freq"


def test_ctr_decay_flagged():
    """CTR halves week-over-week with enough impressions each week → ctr_decay."""
    last_week_start = TODAY - timedelta(days=13)
    this_week_start = TODAY - timedelta(days=7)
    # Last week: 1000 imp / 30 clicks / day = 3% CTR
    last_rows = _week(last_week_start, {
        "impressions": 1000, "reach": 800, "clicks": 30, "spend": 3.0,
    })
    # This week: 1000 imp / 8 clicks / day = 0.8% CTR (73% drop)
    this_rows = _week(this_week_start, {
        "impressions": 1000, "reach": 800, "clicks": 8, "spend": 3.0,
        "purchase_count": 1,
    })
    flags = detect_fatigue(last_rows + this_rows, TODAY)
    assert len(flags) == 1
    assert flags[0].reason == "ctr_decay"
    assert "%" in flags[0].detail


def test_ctr_decay_needs_minimum_impressions_both_weeks():
    """Small sample sizes aren't flagged — avoids noise."""
    last_week_start = TODAY - timedelta(days=13)
    this_week_start = TODAY - timedelta(days=7)
    last_rows = _week(last_week_start, {"impressions": 50, "reach": 50, "clicks": 5})
    this_rows = _week(this_week_start, {"impressions": 50, "reach": 50, "clicks": 1})
    flags = detect_fatigue(last_rows + this_rows, TODAY)
    assert flags == []


def test_weak_ctr_flagged():
    """CTR < 0.5% on 2K+ impressions this week → weak_ctr flag."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 500, "reach": 400, "clicks": 1, "spend": 2.0,
        "purchase_count": 1,  # avoid dead_spend
    })
    # Total week: 3500 impressions, 7 clicks → 0.2% CTR — below 0.5% threshold
    flags = detect_fatigue(rows, TODAY)
    assert len(flags) == 1
    assert flags[0].reason == "weak_ctr"


def test_rules_ordered_dead_spend_wins():
    """Ad matching both dead_spend and high_freq should be tagged dead_spend first."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 2000, "reach": 300, "clicks": 15, "spend": 10.0,
        "purchase_count": 0,
    })
    flags = detect_fatigue(rows, TODAY)
    assert len(flags) == 1
    assert flags[0].reason == "dead_spend"  # wins over high_freq


def test_multiple_ads_each_flagged_independently():
    this_week_start = TODAY - timedelta(days=7)
    rows_a = _week(this_week_start, {
        "impressions": 2000, "reach": 300, "clicks": 15, "spend": 10.0,
    }, ad_id="ad_a")
    rows_b = _week(this_week_start, {
        "impressions": 500, "reach": 400, "clicks": 1, "spend": 2.0,
        "purchase_count": 1,
    }, ad_id="ad_b")
    flags = detect_fatigue(rows_a + rows_b, TODAY)
    assert len(flags) == 2
    reasons = {f.meta_ad_id: f.reason for f in flags}
    assert reasons["ad_a"] == "dead_spend"
    assert reasons["ad_b"] == "weak_ctr"


def test_healthy_ad_not_flagged():
    """Good CTR, good conversions, normal frequency — no flag."""
    this_week_start = TODAY - timedelta(days=7)
    rows = _week(this_week_start, {
        "impressions": 1000, "reach": 800, "clicks": 15, "spend": 3.0,
        "purchase_count": 2,
    })
    assert detect_fatigue(rows, TODAY) == []
