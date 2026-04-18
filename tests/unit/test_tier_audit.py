"""Contracts for Phase 12.5c — AUTO/REVIEW tier audit.

Property-level rules this module enforces:
  1. Never auto-mutate AUTO_ACTIONS. Audit SURFACES evidence to the
     observations table; founder edits the policy by hand.
  2. Demote threshold (10% flagged) requires ≥10 samples.
  3. Small-sample AUTO actions are NOT warned — we wait for evidence.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents import tier_audit


# ── Thresholds are explicit ──

def test_demote_requires_minimum_samples():
    assert tier_audit.DEMOTE_MIN_SAMPLES == 10


def test_demote_threshold_is_ten_percent():
    assert tier_audit.DEMOTE_MAX_FLAG_RATE_PCT == 10.0


def test_promote_thresholds_documented():
    assert tier_audit.PROMOTE_MIN_SAMPLES == 20
    assert tier_audit.PROMOTE_MAX_EDIT_RATE_PCT == 5.0


# ── _demote_warnings behavior ──

@pytest.mark.asyncio
async def test_demote_ignores_review_tier_actions():
    """Founder flagging a REVIEW-tier action is separate from tier demotion
    — you can't demote what's already in REVIEW."""
    rows = [{"action_type": "customer_response", "flagged": True}] * 15
    with patch("src.agents.tier_audit.Database") as mock_db_cls:
        sync = mock_db_cls.return_value
        sync._client.table.return_value.select.return_value.gte.return_value.execute.return_value.data = rows
        warnings = await tier_audit._demote_warnings("2026-04-01T00:00:00+00:00")
    assert warnings == []


@pytest.mark.asyncio
async def test_demote_skips_low_sample_auto_actions():
    """An AUTO action with 5 sends and 2 flagged (40%) is NOT a demote
    warning — we need ≥10 samples before making the call."""
    rows = [{"action_type": "lifecycle_welcome_1", "flagged": True}] * 2 + \
           [{"action_type": "lifecycle_welcome_1", "flagged": False}] * 3
    with patch("src.agents.tier_audit.Database") as mock_db_cls:
        sync = mock_db_cls.return_value
        sync._client.table.return_value.select.return_value.gte.return_value.execute.return_value.data = rows
        warnings = await tier_audit._demote_warnings("since")
    assert warnings == []


@pytest.mark.asyncio
async def test_demote_surfaces_auto_action_above_threshold():
    """lifecycle_welcome_1 in AUTO_ACTIONS with >10% flag rate and ≥10
    samples → warning fires."""
    rows = [{"action_type": "lifecycle_welcome_1", "flagged": True}] * 3 + \
           [{"action_type": "lifecycle_welcome_1", "flagged": False}] * 12
    with patch("src.agents.tier_audit.Database") as mock_db_cls:
        sync = mock_db_cls.return_value
        sync._client.table.return_value.select.return_value.gte.return_value.execute.return_value.data = rows
        warnings = await tier_audit._demote_warnings("since")
    assert len(warnings) == 1
    assert warnings[0]["action_type"] == "lifecycle_welcome_1"
    assert warnings[0]["samples"] == 15
    assert warnings[0]["flagged"] == 3
    assert warnings[0]["flag_rate_pct"] == 20.0


@pytest.mark.asyncio
async def test_demote_handles_empty_data():
    with patch("src.agents.tier_audit.Database") as mock_db_cls:
        sync = mock_db_cls.return_value
        sync._client.table.return_value.select.return_value.gte.return_value.execute.return_value.data = []
        warnings = await tier_audit._demote_warnings("since")
    assert warnings == []


# ── run_audit writes observations, not policy changes ──

@pytest.mark.asyncio
async def test_run_audit_writes_observations_never_mutates_policy():
    """Sanity: the AUTO_ACTIONS list must stay identical after a run."""
    from src.agents.approval_tiers import AUTO_ACTIONS
    before = frozenset(AUTO_ACTIONS)

    with patch("src.agents.tier_audit._promote_candidates", new=AsyncMock(return_value=[])), \
         patch("src.agents.tier_audit._demote_warnings", new=AsyncMock(return_value=[])), \
         patch("src.agents.tier_audit._observe", new=AsyncMock()):
        result = await tier_audit.run_audit()

    after = frozenset(AUTO_ACTIONS)
    assert before == after  # policy untouched
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_audit_observes_each_demote_warning():
    demote_warnings = [
        {"action_type": "lifecycle_welcome_1", "samples": 15,
         "flagged": 3, "flag_rate_pct": 20.0},
        {"action_type": "crafting_update_email", "samples": 11,
         "flagged": 2, "flag_rate_pct": 18.2},
    ]
    with patch("src.agents.tier_audit._promote_candidates",
               new=AsyncMock(return_value=[])), \
         patch("src.agents.tier_audit._demote_warnings",
               new=AsyncMock(return_value=demote_warnings)), \
         patch("src.agents.tier_audit._observe", new=AsyncMock()) as obs:
        await tier_audit.run_audit()
    assert obs.call_count == 2
    # Both observations should carry direction auto→review
    for call in obs.call_args_list:
        assert call.kwargs["data"]["direction"] == "auto→review"
