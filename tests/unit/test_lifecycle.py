"""Tests for Phase 9.2 lifecycle orchestrator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.customer.lifecycle import (
    CARE_GUIDE_DAYS,
    CUSTOM_INQUIRY_DAYS,
    LifecycleCandidate,
    LifecycleOrchestrator,
    REFERRAL_DAYS,
    TRIGGER_ANNIVERSARY,
    TRIGGER_CARE,
    TRIGGER_CUSTOM,
    TRIGGER_REFERRAL,
    _default_subject,
    _fallback_body,
)


@pytest.fixture
def orch():
    with patch("src.customer.lifecycle.AsyncDatabase") as mock_db_cls, \
         patch("src.customer.lifecycle.anthropic.AsyncAnthropic"):
        mock_db_cls.return_value = AsyncMock()
        o = LifecycleOrchestrator()
        yield o


def _order_row(customer_id: int, trigger_sent: dict | None = None, days_since: int = 10) -> dict:
    return {
        "id": 100 + customer_id,
        "shopify_order_id": 5000 + customer_id,
        "total": 4900.0,
        "buyer_email": f"c{customer_id}@example.com",
        "buyer_name": f"Customer {customer_id}",
        "line_items": [{"title": "Diamond Tennis Bracelet"}],
        "customers": {
            "id": customer_id,
            "email": f"c{customer_id}@example.com",
            "name": f"Customer {customer_id}",
            "lifecycle_emails_sent": trigger_sent or {},
        },
    }


@pytest.mark.asyncio
async def test_finds_care_candidates(orch):
    orch._db.get_lifecycle_candidates_from_orders = AsyncMock(
        side_effect=lambda days_since_purchase, window_days: (
            [_order_row(1), _order_row(2)] if days_since_purchase == CARE_GUIDE_DAYS else []
        )
    )
    orch._db.get_anniversary_candidates = AsyncMock(return_value=[])

    candidates = await orch.find_all_candidates()
    care = [c for c in candidates if c.trigger == TRIGGER_CARE]
    assert len(care) == 2
    assert care[0].days_since_purchase == CARE_GUIDE_DAYS
    assert care[0].last_order_items == "Diamond Tennis Bracelet"


@pytest.mark.asyncio
async def test_dedupes_if_trigger_already_sent(orch):
    """A customer who already got care_guide_day10 shouldn't surface again."""
    orch._db.get_lifecycle_candidates_from_orders = AsyncMock(
        side_effect=lambda days_since_purchase, window_days: (
            [_order_row(1, trigger_sent={TRIGGER_CARE: "2026-04-05T00:00:00"})]
            if days_since_purchase == CARE_GUIDE_DAYS else []
        )
    )
    orch._db.get_anniversary_candidates = AsyncMock(return_value=[])

    candidates = await orch.find_all_candidates()
    assert [c for c in candidates if c.trigger == TRIGGER_CARE] == []


@pytest.mark.asyncio
async def test_four_triggers_probed(orch):
    """find_all_candidates must call DB for all three time-based triggers + anniversary."""
    seen_days = []

    async def _spy(days_since_purchase: int, window_days: int = 2) -> list:
        seen_days.append(days_since_purchase)
        return []

    orch._db.get_lifecycle_candidates_from_orders = _spy
    orch._db.get_anniversary_candidates = AsyncMock(return_value=[])

    await orch.find_all_candidates()
    assert set(seen_days) == {CARE_GUIDE_DAYS, REFERRAL_DAYS, CUSTOM_INQUIRY_DAYS}


@pytest.mark.asyncio
async def test_anniversary_candidates_surface(orch):
    orch._db.get_lifecycle_candidates_from_orders = AsyncMock(return_value=[])
    orch._db.get_anniversary_candidates = AsyncMock(return_value=[
        {
            "id": 77,
            "customer_id": 10,
            "customer_email": "bride@example.com",
            "anniversary_date": "2026-06-15",
            "relationship": "wedding_anniversary",
            "_year_key": "year_2026",
            "customers": {"id": 10, "name": "Bride"},
        }
    ])
    candidates = await orch.find_all_candidates()
    assert len(candidates) == 1
    c = candidates[0]
    assert c.trigger == TRIGGER_ANNIVERSARY
    assert c.anniversary_date == "2026-06-15"
    assert c.anniversary_year_key == "year_2026"


@pytest.mark.asyncio
async def test_draft_uses_claude(orch):
    cand = LifecycleCandidate(
        customer_id=1, customer_email="a@b.com", customer_name="Alex",
        trigger=TRIGGER_CARE, days_since_purchase=10,
        last_order_items="Diamond Tennis Bracelet",
    )
    mock_msg = MagicMock(text="A warm care email body.\n\nWarm,\nJaitul")
    mock_response = MagicMock()
    mock_response.content = [mock_msg]
    orch._claude.messages.create = AsyncMock(return_value=mock_response)

    drafted = await orch.draft(cand)
    assert drafted.subject  # subject line set
    assert "care email" in drafted.body.lower() or "Warm" in drafted.body
    orch._claude.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_draft_falls_back_when_claude_errors(orch):
    cand = LifecycleCandidate(
        customer_id=1, customer_email="a@b.com", customer_name="Alex",
        trigger=TRIGGER_REFERRAL, days_since_purchase=60,
    )
    orch._claude.messages.create = AsyncMock(side_effect=Exception("Claude down"))
    drafted = await orch.draft(cand)
    # fallback body contains the referral credit
    assert "$250" in drafted.body
    assert drafted.subject != ""


def test_subject_per_trigger():
    for trigger in [TRIGGER_CARE, TRIGGER_REFERRAL, TRIGGER_CUSTOM, TRIGGER_ANNIVERSARY]:
        c = LifecycleCandidate(
            customer_id=1, customer_email="x@y.com", customer_name="X", trigger=trigger,
        )
        s = _default_subject(c)
        assert s
        assert s != "From Pinaka" or trigger not in [TRIGGER_CARE, TRIGGER_REFERRAL]


def test_anniversary_fallback_body_mentions_date():
    c = LifecycleCandidate(
        customer_id=1, customer_email="x@y.com", customer_name="X",
        trigger=TRIGGER_ANNIVERSARY, anniversary_date="2026-06-15",
        relationship="wedding_anniversary",
    )
    body = _fallback_body(c)
    assert "2026-06-15" in body
    assert "anniversary" in body.lower()


def test_referral_fallback_body_has_credit():
    c = LifecycleCandidate(
        customer_id=1, customer_email="x@y.com", customer_name="X",
        trigger=TRIGGER_REFERRAL, days_since_purchase=60,
    )
    body = _fallback_body(c)
    assert "$250" in body
