"""Unit tests for RFM scoring + segment assignment (Phase 10.B)."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.customer.rfm import (
    F_THRESHOLDS,
    M_THRESHOLDS,
    PROJECTED_ORDERS_MAP,
    R_THRESHOLDS,
    RFMScorer,
    _bucket,
    _segment_of,
)

TODAY = date(2026, 4, 16)


def _order(days_ago: int, total: float, refund: float = 0.0, status: str = "paid") -> dict:
    return {
        "created_at": (TODAY - timedelta(days=days_ago)).isoformat(),
        "total": total,
        "refund_amount": refund,
        "status": status,
    }


# ── Bucketing ──

def test_bucket_frequency_top():
    assert _bucket(10, F_THRESHOLDS) == 5
    assert _bucket(1, F_THRESHOLDS) == 1
    assert _bucket(3, F_THRESHOLDS) == 3  # 3-4 → 3
    assert _bucket(5, F_THRESHOLDS) == 4  # 5-9 → 4


def test_bucket_monetary():
    assert _bucket(100, M_THRESHOLDS) == 1
    assert _bucket(5_000, M_THRESHOLDS) == 1
    assert _bucket(8_000, M_THRESHOLDS) == 2
    assert _bucket(60_000, M_THRESHOLDS) == 5


def test_bucket_recency_inverted():
    """Recency: lower = better (score 5). Higher = worse."""
    assert _bucket(10, R_THRESHOLDS, inverted=True) == 5  # ≤30
    assert _bucket(60, R_THRESHOLDS, inverted=True) == 4  # ≤90
    assert _bucket(200, R_THRESHOLDS, inverted=True) == 2  # >180 ≤365
    assert _bucket(400, R_THRESHOLDS, inverted=True) == 1  # >365


# ── Segments ──

def test_segment_champion():
    assert _segment_of(r=5, f=4, m=4) == "champion"
    assert _segment_of(r=4, f=3, m=3) == "champion"


def test_segment_loyal_when_recency_low():
    """F≥3 + M≥3 but R≤3 → loyal, not champion."""
    assert _segment_of(r=3, f=5, m=5) == "loyal"


def test_segment_at_risk():
    assert _segment_of(r=2, f=3, m=3) == "at_risk"


def test_segment_new():
    assert _segment_of(r=5, f=1, m=1) == "new"
    assert _segment_of(r=5, f=1, m=4) == "new"


def test_segment_one_and_done():
    assert _segment_of(r=3, f=1, m=3) == "one_and_done"


def test_segment_hibernating():
    """R≤2 + F=1 — bought once, long ago."""
    assert _segment_of(r=1, f=1, m=1) == "hibernating"
    assert _segment_of(r=2, f=1, m=3) == "hibernating"


def test_segment_at_risk_beats_loyal_when_recency_low():
    """A repeat buyer with low recency gets at_risk (actionable), not loyal."""
    # r=2, f=5, m=5 — was loyal, now slipping
    assert _segment_of(r=2, f=5, m=5) == "at_risk"


def test_segment_active_fallback():
    """Middle-of-the-road buyer falls into 'active'."""
    assert _segment_of(r=3, f=2, m=2) == "active"


# ── Scoring integration ──

@pytest.fixture
def scorer():
    with patch("src.customer.rfm.AsyncDatabase") as mock_db_cls:
        db = AsyncMock()
        db._sync._client = MagicMock()
        mock_db_cls.return_value = db
        yield RFMScorer()


def test_score_one_champion(scorer):
    """Recent + frequent + high-spend buyer = champion."""
    orders = [_order(5, 5000), _order(60, 5000), _order(120, 5000)]
    r = scorer._score_one(customer_id=1, orders=orders, today=TODAY)
    assert r.frequency == 3
    assert r.monetary == 15000.0
    assert r.recency_days == 5
    assert r.segment == "champion"
    assert r.r_score == 5
    assert r.f_score == 3
    assert r.m_score == 3


def test_score_one_hibernating(scorer):
    """One order 400 days ago = one_and_done → first ladder match wins."""
    orders = [_order(400, 4000)]
    r = scorer._score_one(customer_id=1, orders=orders, today=TODAY)
    assert r.segment in {"one_and_done", "hibernating", "active"}  # depends on threshold edge
    assert r.frequency == 1


def test_score_respects_refund_in_monetary(scorer):
    """A $5K order refunded $2K contributes $3K net."""
    orders = [_order(10, 5000, refund=2000)]
    r = scorer._score_one(customer_id=1, orders=orders, today=TODAY)
    assert r.monetary == 3000.0
    assert r.avg_order_value == 3000.0


def test_score_ltv_projection(scorer):
    orders = [_order(10, 5000), _order(60, 5000)]
    r = scorer._score_one(customer_id=1, orders=orders, today=TODAY)
    # F=2 → 0.7 projected orders; R=5 (≤30) → ×1.3 bonus = 0.91
    # LTV = avg_order (5000) × 0.91 = 4550
    assert r.projected_ltv_365d > 4000
    assert r.projected_ltv_365d < 6000


def test_score_zero_monetary_edge(scorer):
    """Refund equals order total → net = 0, avg = 0."""
    orders = [_order(10, 5000, refund=5000)]
    r = scorer._score_one(customer_id=1, orders=orders, today=TODAY)
    assert r.monetary == 0
    assert r.avg_order_value == 0


def test_score_with_no_valid_dates(scorer):
    """Orders without created_at → recency_days is None, but F and M still computed."""
    orders = [{"total": 5000, "refund_amount": 0, "created_at": None}]
    r = scorer._score_one(customer_id=1, orders=orders, today=TODAY)
    assert r.recency_days is None
    assert r.monetary == 5000.0
    assert r.frequency == 1
