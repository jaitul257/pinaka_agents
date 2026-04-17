"""Contracts for Phase 13.2 entity memory — the llm-wiki pattern.

The whole point: raw Supabase is the immutable log, entity_memory is the
compiled wiki. Break one of these contracts and the agent's mental model
of a customer/product drifts from truth.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents import memory as mem


# ── Type + ID validation ──

@pytest.mark.asyncio
async def test_get_memory_rejects_unknown_entity_type(caplog):
    result = await mem.get_memory("widget", "123")
    assert result is None


def test_fallback_customer_note_handles_empty_customer():
    """Fallback must not crash on incomplete data — it's the safety net."""
    note = mem._fallback_customer_note({"customer": [{}], "orders": [],
                                         "messages": [], "rfm": []})
    assert "Who" in note
    assert len(note) <= mem.MAX_CONTENT_CHARS


def test_fallback_product_note_minimum_output():
    note = mem._fallback_product_note({"product": None, "orders_with_sku": [],
                                       "ad_creatives": []})
    assert "Summary" in note


def test_fallback_seasonal_note_no_data():
    note = mem._fallback_seasonal_note("04", {"daily_stats": []})
    assert "04" in note


# ── SKU filtering on line items ──

def test_sku_in_line_items_direct_match():
    li = [{"sku": "AB0025", "title": "Bracelet"}]
    assert mem._sku_in_line_items("AB0025", li) is True


def test_sku_in_line_items_nested_variant():
    li = [{"variant": {"sku": "AB0025-YG-7"}}]
    assert mem._sku_in_line_items("AB0025-YG-7", li) is True


def test_sku_in_line_items_no_match():
    li = [{"sku": "OTHER"}]
    assert mem._sku_in_line_items("AB0025", li) is False


def test_sku_in_line_items_null_safe():
    assert mem._sku_in_line_items("X", None) is False
    assert mem._sku_in_line_items("X", []) is False


# ── Compile flow returns None when no data ──

@pytest.mark.asyncio
async def test_compile_customer_skips_when_no_data():
    """Customer with no orders / messages / lifecycle events → no note.

    We don't compile empty notes — they'd waste token budget and mislead
    agents into thinking there's memory when there isn't.
    """
    async def fake_gather(_cid):
        return {"customer": [], "orders": [], "messages": [],
                "rfm": [], "lifecycle_events": []}

    with patch("src.agents.memory._gather_customer_raw", fake_gather):
        result = await mem.compile_customer(999999)
    assert result is None


@pytest.mark.asyncio
async def test_compile_product_skips_when_fully_empty():
    async def fake_gather(_sku):
        return {"product": None, "orders_with_sku": [],
                "ad_creatives": [], "ad_metrics": []}

    with patch("src.agents.memory._gather_product_raw", fake_gather):
        result = await mem.compile_product("DOES-NOT-EXIST")
    assert result is None


@pytest.mark.asyncio
async def test_compile_seasonal_rejects_bad_month():
    result = await mem.compile_seasonal("13")
    assert result is None
    result = await mem.compile_seasonal("April")
    assert result is None


@pytest.mark.asyncio
async def test_compile_seasonal_skips_when_no_stats():
    async def fake_gather(_m):
        return {"daily_stats": []}

    with patch("src.agents.memory._gather_seasonal_raw", fake_gather):
        result = await mem.compile_seasonal("04")
    assert result is None


# ── Max content length is enforced ──

@pytest.mark.asyncio
async def test_fallback_never_exceeds_max_chars():
    """If 200 orders come in, the fallback still respects the cap."""
    big_orders = [{"created_at": "2026-04-01T00:00:00",
                   "total": 5000, "status": "delivered"} for _ in range(200)]
    raw = {"customer": [{"email": "test@x.com", "lifetime_value": 1000000,
                         "last_segment": "champion"}],
           "orders": big_orders, "messages": [], "rfm": []}
    note = mem._fallback_customer_note(raw)
    assert len(note) <= mem.MAX_CONTENT_CHARS


# ── Staleness check ──

@pytest.mark.asyncio
async def test_needs_recompile_when_missing():
    with patch("src.agents.memory.get_memory", AsyncMock(return_value=None)):
        assert await mem._needs_recompile("customer", "42") is True


@pytest.mark.asyncio
async def test_needs_recompile_when_fresh():
    """<24h old note should NOT be recompiled (unless caller overrides)."""
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with patch("src.agents.memory.get_memory",
               AsyncMock(return_value={"compiled_at": fresh})):
        assert await mem._needs_recompile("customer", "42") is False


@pytest.mark.asyncio
async def test_needs_recompile_when_stale():
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    with patch("src.agents.memory.get_memory",
               AsyncMock(return_value={"compiled_at": stale})):
        assert await mem._needs_recompile("customer", "42") is True
