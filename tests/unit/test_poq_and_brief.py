"""Unit tests for PieceOfQuarter + DashboardBrief aggregator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.customer.piece_of_quarter import (
    PieceOfQuarter,
    _current_quarter_key,
    _fallback_body,
)
from src.dashboard.brief import DashboardBrief, _aggregate_by_ad, _fallback_narrative


# ── PieceOfQuarter ──

@pytest.fixture
def poq():
    with patch("src.customer.piece_of_quarter.AsyncDatabase") as mock_db_cls, \
         patch("src.customer.piece_of_quarter.anthropic.AsyncAnthropic"):
        mock_db = AsyncMock()
        mock_db._sync._client = MagicMock()
        mock_db_cls.return_value = mock_db
        yield PieceOfQuarter()


def test_quarter_key_format():
    key = _current_quarter_key()
    assert key.count("-") == 1
    year, q = key.split("-")
    assert len(year) == 4
    assert q.startswith("Q") and int(q[1:]) in (1, 2, 3, 4)


def test_fallback_body_mentions_featured():
    body = _fallback_body("Pink Diamond Tennis Bracelet")
    assert "Pink Diamond Tennis Bracelet" in body
    assert "Warm," in body
    assert "Jaitul" in body


@pytest.mark.asyncio
async def test_draft_with_claude(poq):
    # build_audience returns empty-ish list; we're testing the draft text
    poq.build_audience = AsyncMock(return_value=[
        {"id": 1, "email": "a@b.com", "name": "A"},
        {"id": 2, "email": "c@d.com", "name": "C"},
    ])
    poq.pick_featured_piece = AsyncMock(return_value="Yellow Gold Bracelet")
    mock_resp = MagicMock()
    mock_msg = MagicMock(text=json.dumps({
        "subject": "Something new this quarter",
        "body": "Short email body about a new piece.",
    }))
    mock_resp.content = [mock_msg]
    poq._claude.messages.create = AsyncMock(return_value=mock_resp)

    draft = await poq.draft()
    assert draft.subject == "Something new this quarter"
    assert "new piece" in draft.body
    assert draft.featured_piece == "Yellow Gold Bracelet"
    assert draft.audience_count == 2


@pytest.mark.asyncio
async def test_draft_falls_back_on_claude_error(poq):
    poq.build_audience = AsyncMock(return_value=[])
    poq.pick_featured_piece = AsyncMock(return_value="X Piece")
    poq._claude.messages.create = AsyncMock(side_effect=Exception("boom"))

    draft = await poq.draft()
    assert "X Piece" in draft.body
    assert draft.audience_count == 0


@pytest.mark.asyncio
async def test_pick_featured_prefers_top_spend(poq):
    from datetime import date
    poq._db.get_creative_metrics_range = AsyncMock(return_value=[
        {"ad_name": "Ad A", "spend": 10.0},
        {"ad_name": "Ad B", "spend": 50.0},
        {"ad_name": "Ad B", "spend": 20.0},
    ])
    name = await poq.pick_featured_piece()
    assert name == "Ad B"


@pytest.mark.asyncio
async def test_send_batch_tallies_result(poq):
    poq.build_audience = AsyncMock(return_value=[
        {"email": "a@b.com", "name": "A"},
        {"email": "c@d.com", "name": "C"},
        {"email": "", "name": "blank"},  # should be skipped
    ])
    mock_sender = MagicMock()
    mock_sender.send_lifecycle_email = MagicMock(side_effect=[True, False])

    with patch("src.core.email.EmailSender", return_value=mock_sender):
        result = await poq.send_batch(subject="s", body="b")
    assert result["audience"] == 3
    assert result["sent"] == 1
    assert result["failed"] == 1


# ── DashboardBrief ──

@pytest.fixture
def brief_agg():
    with patch("src.dashboard.brief.AsyncDatabase") as mock_db_cls, \
         patch("src.dashboard.brief.anthropic.AsyncAnthropic"):
        mock_db = AsyncMock()
        mock_db._sync._client = MagicMock()
        mock_db_cls.return_value = mock_db
        yield DashboardBrief()


def test_aggregate_by_ad_sums_correctly():
    rows = [
        {"meta_ad_id": "A", "ad_name": "Ad A", "impressions": 1000,
         "clicks": 20, "spend": 10.0, "purchase_count": 1},
        {"meta_ad_id": "A", "ad_name": "Ad A", "impressions": 2000,
         "clicks": 30, "spend": 20.0, "purchase_count": 0},
        {"meta_ad_id": "B", "ad_name": "Ad B", "impressions": 500,
         "clicks": 2, "spend": 5.0, "purchase_count": 0},
    ]
    result = _aggregate_by_ad(rows)
    a = next(r for r in result if r["name"] == "Ad A")
    b = next(r for r in result if r["name"] == "Ad B")
    assert a["impressions"] == 3000
    assert a["purchases"] == 1
    assert a["ctr"] == round(50 / 3000 * 100, 2)
    assert b["ctr"] == round(2 / 500 * 100, 2)


def test_aggregate_by_ad_skips_zero_impressions():
    rows = [
        {"meta_ad_id": "X", "ad_name": "X", "impressions": 0,
         "clicks": 0, "spend": 0.0, "purchase_count": 0},
    ]
    assert _aggregate_by_ad(rows) == []


def test_fallback_narrative_healthy_mer():
    from src.dashboard.brief import BriefData
    from datetime import datetime as _dt
    brief = BriefData(generated_at=_dt.utcnow(), mer_14d=4.5)
    narr = _fallback_narrative(brief)
    assert "4.5x" in narr
    assert "healthy" in narr.lower()


def test_fallback_narrative_leaking_mer():
    from src.dashboard.brief import BriefData
    from datetime import datetime as _dt
    brief = BriefData(generated_at=_dt.utcnow(), mer_14d=1.2)
    narr = _fallback_narrative(brief)
    assert "leaking" in narr.lower()


@pytest.mark.asyncio
async def test_build_happy_path(brief_agg):
    brief_agg._db.get_stats_range = AsyncMock(return_value=[
        {"revenue": 10000, "ad_spend_meta": 2500, "ad_spend_google": 0},
        {"revenue": 5000, "ad_spend_meta": 1000, "ad_spend_google": 0},
    ])
    brief_agg._db.get_creative_metrics_range = AsyncMock(return_value=[
        {"meta_ad_id": "A", "ad_name": "Variant A", "impressions": 1000,
         "clicks": 15, "spend": 20.0, "purchase_count": 1},
        {"meta_ad_id": "B", "ad_name": "Variant B", "impressions": 2000,
         "clicks": 10, "spend": 15.0, "purchase_count": 0},
    ])
    # Observations: mock the thread query
    import asyncio
    async def _fake_to_thread(fn, *a, **kw):
        return MagicMock(data=[
            {"severity": "critical", "summary": "A critical thing",
             "category": "marketing", "created_at": "2026-04-16T00:00"},
            {"severity": "warning", "summary": "A warning",
             "category": "order", "created_at": "2026-04-16T00:00"},
        ])
    with patch("asyncio.to_thread", _fake_to_thread):
        brief_agg._db.get_welcome_candidates = AsyncMock(return_value=[])
        # Mock LifecycleOrchestrator
        with patch("src.customer.lifecycle.LifecycleOrchestrator") as mock_orch_cls:
            mock_orch = AsyncMock()
            mock_orch.find_all_candidates.return_value = [MagicMock(), MagicMock()]
            mock_orch_cls.return_value = mock_orch
            # Claude narrative
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text="A three-paragraph narrative about the brief.")]
            brief_agg._claude.messages.create = AsyncMock(return_value=mock_resp)
            brief = await brief_agg.build()

    assert brief.mer_14d == round(15000 / 3500, 2)
    assert brief.revenue_14d == 15000.0
    assert brief.spend_14d == 3500.0
    assert brief.creative_count == 2
    assert brief.critical_count == 1
    assert brief.warning_count == 1
    assert brief.pending_lifecycle_candidates == 2
    assert "narrative" in brief.narrative.lower()
