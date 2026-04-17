"""Contracts for Phase 13.4 — agent rolling memory compilation.

These guard against two specific failure modes the research flagged:
  1. Compiling an empty note (no activity) — wastes tokens, misleads.
  2. Growing beyond the hard size cap — defeats the whole point of
     compacting.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.agents import memory as mem
from src.agents.base import BaseAgent


# ── Agent type is recognized ──

def test_agent_is_supported_type():
    assert "agent" in mem.SUPPORTED_TYPES


# ── Empty activity → no compile ──

@pytest.mark.asyncio
async def test_compile_agent_skips_when_no_activity():
    """Agent with no recent audit rows / outcomes / auto_sent → no note.

    Writing an empty 'I did nothing' note wastes Claude tokens AND makes
    the agent think it has context when it doesn't. Skip is correct."""
    async def fake_gather(*_a, **_kw):
        return {"audit_log": [], "auto_sent": [], "outcomes": [], "observations": []}

    with patch("src.agents.memory._gather_agent_raw", fake_gather):
        result = await mem.compile_agent("marketing")
    assert result is None


# ── Fallback note shape ──

def test_fallback_agent_note_counts_runs():
    raw = {
        "audit_log": [
            {"result": "success", "escalated": False},
            {"result": "success", "escalated": False},
            {"result": "failed", "escalated": True},
        ],
        "auto_sent": [{"flagged": False}, {"flagged": True}],
        "outcomes": [{"outcome_type": "email_clicked"}, {"outcome_type": "email_clicked"}],
        "observations": [],
    }
    note = mem._fallback_agent_note("retention", raw, lookback_days=7)
    assert "3 runs" in note
    assert "1 escalated" in note
    assert "2 succeeded" in note
    assert "2 auto-sent" in note
    assert "1 flagged" in note
    assert "email_clicked: 2" in note


def test_fallback_agent_note_respects_content_cap():
    """Even with a huge raw payload the fallback stays under the cap."""
    big_audit = [{"result": "success", "escalated": False} for _ in range(1000)]
    note = mem._fallback_agent_note(
        "order_ops", {"audit_log": big_audit, "auto_sent": [],
                      "outcomes": [], "observations": []},
        lookback_days=7,
    )
    assert len(note) <= mem.MAX_CONTENT_CHARS


# ── Compile agent accepts missing activity buckets ──

@pytest.mark.asyncio
async def test_compile_agent_compiles_when_only_outcomes_exist():
    """Outcomes alone is enough signal to compile. A passive agent that
    only has program-verified outcomes recorded against its name should
    still get a memory note."""
    fake_raw = {
        "audit_log": [],
        "auto_sent": [],
        "outcomes": [{"outcome_type": "email_delivered",
                      "outcome_value": {}, "action_type": "welcome_1",
                      "entity_type": "customer", "entity_id": "1",
                      "fired_at": "2026-04-17T00:00:00+00:00"}],
        "observations": [],
    }

    async def fake_gather(*_a, **_kw):
        return fake_raw

    async def fake_upsert(**kw):
        return {"entity_type": kw["entity_type"], "entity_id": kw["entity_id"],
                "content_len": len(kw["content"])}

    with patch("src.agents.memory._gather_agent_raw", fake_gather), \
         patch("src.agents.memory._upsert_memory", side_effect=fake_upsert), \
         patch("src.agents.memory.settings") as mock_settings:
        mock_settings.anthropic_api_key = ""  # force fallback path
        result = await mem.compile_agent("retention")

    assert result is not None
    assert result["entity_id"] == "retention"


# ── BaseAgent.get_my_memory tool is wired ──

def test_base_agent_registers_get_my_memory():
    """Every BaseAgent subclass inherits the self-memory tool. Regression
    guard: if someone removes _register_base_tools() or forgets to call
    super().__init__(), agents silently lose this capability."""
    agent = BaseAgent()
    names = [t["name"] for t in agent.tools.list_tools()] if hasattr(agent.tools, "list_tools") else []
    # The ToolRegistry's internal storage — if list_tools isn't available,
    # check via hasattr on the registry's _tools or equivalent.
    if not names:
        # Fall back to the _tools internal dict convention used elsewhere
        internal = getattr(agent.tools, "_tools", None) or getattr(agent.tools, "tools", None) or {}
        names = list(internal.keys()) if hasattr(internal, "keys") else []
    assert "get_my_memory" in names, f"get_my_memory missing; found: {names}"


# ── Orchestrator: _AGENT_NAMES kept in sync ──

def test_agent_names_matches_kpi_map_keys():
    """If you added a new agent with a KPI, its self-memory should also
    be compiled nightly. This assertion forces that pair to stay in sync.
    """
    from src.agents.kpis import AGENT_KPI_MAP
    assert set(mem._AGENT_NAMES) == set(AGENT_KPI_MAP.keys())
