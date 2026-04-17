"""Contracts for Phase 13.1 — program-verified outcomes.

These lock in the rules that prevent this module from drifting into
LLM-judged territory. If you're tempted to remove a test, ask whether
the outcome you're replacing it with is actually program-verifiable.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.agents import outcomes


# ── Taxonomy is closed, not open ──

def test_unknown_outcome_type_is_rejected():
    """Can't record an outcome type we haven't pre-declared. Prevents
    agents sneaking in LLM-judged signals like 'customer_was_happy'."""
    import asyncio

    async def _run():
        return await outcomes.record(
            agent_name="marketing",
            action_type="whatever",
            outcome_type="customer_was_delighted",  # not in OUTCOME_TYPES
        )

    assert asyncio.run(_run()) is None


def test_polarity_matches_taxonomy():
    """Every declared outcome type has a polarity. Missing polarity = bug."""
    for otype in outcomes.OUTCOME_TYPES:
        assert otype in outcomes.OUTCOME_POLARITY, f"{otype} missing polarity"


def test_polarity_uses_ternary_not_ratings():
    """Polarity is -1 / 0 / +1. No "score out of 10" — that would invite
    subjective scoring and we're deterministic-only."""
    for otype, pol in outcomes.OUTCOME_POLARITY.items():
        assert pol in (-1, 0, 1), f"{otype} has non-ternary polarity {pol}"


# ── SendGrid event mapping ──

def test_sendgrid_delivered_maps_to_email_delivered():
    assert outcomes._SENDGRID_EVENT_TO_OUTCOME["delivered"] == "email_delivered"


def test_sendgrid_dropped_counts_as_bounce():
    """We don't need a separate 'dropped' outcome — deliverability failure is
    deliverability failure."""
    assert outcomes._SENDGRID_EVENT_TO_OUTCOME["dropped"] == "email_bounced"


def test_sendgrid_processed_and_deferred_are_ignored():
    """Only terminal SG events become outcomes. Intermediate states
    ('processed', 'deferred') would double-count."""
    assert "processed" not in outcomes._SENDGRID_EVENT_TO_OUTCOME
    assert "deferred" not in outcomes._SENDGRID_EVENT_TO_OUTCOME


# ── Agent inference fallback ──

def test_infer_agent_from_welcome_category():
    assert outcomes._infer_agent_from_category({"category": "welcome_1"}) == "retention"


def test_infer_agent_from_crafting_category():
    assert outcomes._infer_agent_from_category({"category": "crafting_update"}) == "order_ops"


def test_infer_agent_from_support_category():
    assert outcomes._infer_agent_from_category({"category": "customer_service_reply"}) == "customer_service"


def test_infer_agent_unknown_returns_unknown():
    assert outcomes._infer_agent_from_category({"category": "mystery"}) == "unknown"


def test_infer_agent_handles_list_category():
    """SendGrid sometimes sends category as a list."""
    assert outcomes._infer_agent_from_category({"category": ["welcome_1", "lifecycle"]}) == "retention"


# ── Business-day math (must match the 15-day SLA) ──

def test_biz_days_zero_on_same_day():
    t = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)  # Monday
    assert outcomes._biz_days_between(t, t) == 0


def test_biz_days_skips_weekend():
    mon = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)  # Monday
    next_mon = mon + timedelta(days=7)
    # Mon→Mon = 5 weekdays between (Tue/Wed/Thu/Fri + next Mon)
    assert outcomes._biz_days_between(mon, next_mon) == 5


def test_biz_days_returns_zero_when_reversed():
    t = datetime(2026, 4, 13, tzinfo=timezone.utc)
    assert outcomes._biz_days_between(t, t - timedelta(days=5)) == 0


# ── Idempotency key derivation ──

def test_derive_idempotency_key_is_stable():
    k1 = outcomes.derive_idempotency_key("order_shipped_on_time", "12345", "2026-04-13")
    k2 = outcomes.derive_idempotency_key("order_shipped_on_time", "12345", "2026-04-13")
    assert k1 == k2


def test_derive_idempotency_key_varies_by_entity():
    k1 = outcomes.derive_idempotency_key("order_shipped_on_time", "12345", "2026-04-13")
    k2 = outcomes.derive_idempotency_key("order_shipped_on_time", "67890", "2026-04-13")
    assert k1 != k2


def test_derive_idempotency_key_varies_by_type():
    k1 = outcomes.derive_idempotency_key("order_shipped_on_time", "12345", "2026-04-13")
    k2 = outcomes.derive_idempotency_key("order_shipped_late",    "12345", "2026-04-13")
    assert k1 != k2


# ── SendGrid events empty list is a no-op, not an error ──

@pytest.mark.asyncio
async def test_record_sendgrid_events_empty():
    result = await outcomes.record_sendgrid_events([])
    assert result == {"accepted": 0, "deduped": 0, "ignored": 0, "total": 0}


@pytest.mark.asyncio
async def test_record_sendgrid_events_ignores_unknown_event_type():
    """Non-terminal SG events are counted as ignored, not silently accepted."""
    with patch.object(outcomes, "record", new=AsyncMock(return_value=None)) as rec:
        result = await outcomes.record_sendgrid_events([
            {"event": "processed", "email": "x@y.com", "sg_event_id": "sg1"},
            {"event": "deferred",  "email": "x@y.com", "sg_event_id": "sg2"},
        ])
    assert result["ignored"] == 2
    assert result["accepted"] == 0
    rec.assert_not_called()


@pytest.mark.asyncio
async def test_record_sendgrid_events_uses_sg_event_id_as_idempotency():
    with patch.object(outcomes, "record", new=AsyncMock(return_value=1)) as rec:
        await outcomes.record_sendgrid_events([
            {"event": "delivered", "email": "x@y.com",
             "sg_event_id": "abc123", "timestamp": 1700000000,
             "custom_args": {"agent_name": "retention", "action_type": "welcome_1"}},
        ])
    call_kwargs = rec.call_args.kwargs
    assert call_kwargs["idempotency_key"] == "sg:abc123"
    assert call_kwargs["agent_name"] == "retention"
    assert call_kwargs["action_type"] == "welcome_1"
    assert call_kwargs["outcome_type"] == "email_delivered"


# ── Rollup shape ──

@pytest.mark.asyncio
async def test_rollup_counts_by_agent_and_type():
    fake_rows = [
        {"agent_name": "retention", "outcome_type": "email_delivered"},
        {"agent_name": "retention", "outcome_type": "email_delivered"},
        {"agent_name": "retention", "outcome_type": "email_clicked"},
        {"agent_name": "order_ops", "outcome_type": "order_shipped_on_time"},
    ]
    with patch("src.agents.outcomes.Database") as mock_db_cls:
        mock_client = mock_db_cls.return_value._client
        mock_client.table.return_value.select.return_value.gte.return_value.execute.return_value.data = fake_rows
        result = await outcomes.rollup_by_agent(days=30)

    assert result["retention"]["email_delivered"] == 2
    assert result["retention"]["email_clicked"] == 1
    assert result["order_ops"]["order_shipped_on_time"] == 1
