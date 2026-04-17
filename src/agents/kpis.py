"""Agent-owned KPIs — each agent has one north-star metric.

Phase 12.3 of the ownership layer. Makes "ownership" measurable.
Computed daily by /cron/compute-agent-kpis, surfaced on
/dashboard/agents, and used in weekly retros.

Definitions:
    Marketing         → MER (Marketing Efficiency Ratio) = revenue / ad_spend
    Retention         → repeat_rate = returning_customers / total_customers (90d)
    Customer Service  → resolution_hours = median hours from msg received to reply
    Finance           → net_margin_pct = (revenue - cogs - fees) / revenue (30d)
    Order Ops         → on_time_ship_rate = shipped_within_15bd / shipped (30d)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.core.database import AsyncDatabase, Database

logger = logging.getLogger(__name__)


AGENT_KPI_MAP: dict[str, dict[str, str]] = {
    "marketing":        {"kpi_name": "mer",                 "unit": "x",    "higher_is_better": "true"},
    "retention":        {"kpi_name": "repeat_rate",         "unit": "%",    "higher_is_better": "true"},
    "customer_service": {"kpi_name": "resolution_hours",    "unit": "hrs",  "higher_is_better": "false"},
    "finance":          {"kpi_name": "net_margin_pct",      "unit": "%",    "higher_is_better": "true"},
    "order_ops":        {"kpi_name": "on_time_ship_rate",   "unit": "%",    "higher_is_better": "true"},
}


async def compute_all(db: AsyncDatabase | None = None) -> dict[str, Any]:
    """Compute today's KPI for every agent, upsert into agent_kpis. One-shot."""
    db = db or AsyncDatabase()
    today = date.today()
    results: dict[str, Any] = {}
    for agent_name, meta in AGENT_KPI_MAP.items():
        try:
            value = await _compute_for(agent_name, db)
        except Exception:
            logger.exception("kpi compute failed for %s", agent_name)
            continue
        if value is None:
            results[agent_name] = {"status": "no_data"}
            continue
        trend_7d, trend_30d = await _trends(agent_name, meta["kpi_name"], value)
        await _upsert_kpi(agent_name, meta["kpi_name"], value, trend_7d, trend_30d, today)
        results[agent_name] = {
            "kpi_name": meta["kpi_name"],
            "value": round(value, 3),
            "trend_7d": trend_7d,
            "trend_30d": trend_30d,
        }
    return results


async def _compute_for(agent_name: str, db: AsyncDatabase) -> float | None:
    if agent_name == "marketing":
        return await _mer_30d(db)
    if agent_name == "retention":
        return await _repeat_rate_90d(db)
    if agent_name == "customer_service":
        return await _resolution_hours_30d(db)
    if agent_name == "finance":
        return await _net_margin_pct_30d(db)
    if agent_name == "order_ops":
        return await _on_time_ship_rate_30d(db)
    return None


# ── Individual KPI computations ──

async def _mer_30d(db: AsyncDatabase) -> float | None:
    """Revenue / ad_spend over last 30d."""
    today = date.today()
    stats = await db.get_stats_range(today - timedelta(days=30), today)
    if not stats:
        return None
    revenue = sum(float(s.get("revenue") or 0) for s in stats)
    spend = sum(float(s.get("ad_spend_google") or 0) + float(s.get("ad_spend_meta") or 0)
                for s in stats)
    if spend <= 0:
        return None
    return revenue / spend


async def _repeat_rate_90d(db: AsyncDatabase) -> float | None:
    """% of customers in 90d who had 2+ orders."""
    since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    def _query():
        sync = Database()
        res = (sync._client.table("orders")
               .select("customer_id")
               .gte("created_at", since)
               .execute())
        return res.data or []

    rows = await asyncio.to_thread(_query)
    if not rows:
        return None
    from collections import Counter
    counts = Counter(r["customer_id"] for r in rows if r.get("customer_id"))
    if not counts:
        return None
    repeaters = sum(1 for c in counts.values() if c >= 2)
    return (repeaters / len(counts)) * 100.0


async def _resolution_hours_30d(db: AsyncDatabase) -> float | None:
    """Median hours from message received → reply sent (last 30d)."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    def _query():
        sync = Database()
        # Assumes `messages` has (received_at, responded_at) columns; fall back to created_at+replied_at
        res = (sync._client.table("messages")
               .select("created_at,responded_at,replied_at")
               .gte("created_at", since)
               .execute())
        return res.data or []

    rows = await asyncio.to_thread(_query)
    deltas: list[float] = []
    for r in rows:
        started = r.get("created_at")
        ended = r.get("responded_at") or r.get("replied_at")
        if not started or not ended:
            continue
        try:
            s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            e = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            deltas.append((e - s).total_seconds() / 3600.0)
        except Exception:
            continue
    if not deltas:
        return None
    deltas.sort()
    mid = len(deltas) // 2
    return deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2


async def _net_margin_pct_30d(db: AsyncDatabase) -> float | None:
    """Rough net margin: (revenue - ad_spend - shopify_fees_est) / revenue."""
    today = date.today()
    stats = await db.get_stats_range(today - timedelta(days=30), today)
    if not stats:
        return None
    revenue = sum(float(s.get("revenue") or 0) for s in stats)
    if revenue <= 0:
        return None
    spend = sum(float(s.get("ad_spend_google") or 0) + float(s.get("ad_spend_meta") or 0)
                for s in stats)
    orders = sum(int(s.get("order_count") or 0) for s in stats)
    shopify_fees = revenue * 0.029 + (orders * 0.30)  # 2.9% + $0.30
    # Material cost is unknown at this layer — approximate at 35% of revenue
    # (Pinaka's diamond+gold COGS target per founder conversations)
    cogs_est = revenue * 0.35
    net = revenue - spend - shopify_fees - cogs_est
    return (net / revenue) * 100.0


async def _on_time_ship_rate_30d(db: AsyncDatabase) -> float | None:
    """% of orders shipped within 15 business days of creation (last 30d)."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    def _query():
        sync = Database()
        res = (sync._client.table("orders")
               .select("created_at,shipped_at")
               .gte("created_at", since)
               .execute())
        return res.data or []

    rows = await asyncio.to_thread(_query)
    if not rows:
        return None
    shipped = [r for r in rows if r.get("shipped_at")]
    if not shipped:
        return None
    on_time = 0
    for r in shipped:
        try:
            c = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
            s = datetime.fromisoformat(str(r["shipped_at"]).replace("Z", "+00:00"))
            biz_days = _business_days_between(c, s)
            if biz_days <= 15:
                on_time += 1
        except Exception:
            continue
    return (on_time / len(shipped)) * 100.0


def _business_days_between(a: datetime, b: datetime) -> int:
    # Crude: count Mon-Fri between the two dates
    if b < a:
        return 0
    days = 0
    cur = a.date()
    end = b.date()
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


# ── Storage + trends ──

async def _upsert_kpi(
    agent_name: str, kpi_name: str, value: float,
    trend_7d: float | None, trend_30d: float | None, for_date: date,
) -> None:
    def _up():
        sync = Database()
        return sync._client.table("agent_kpis").upsert({
            "agent_name": agent_name,
            "kpi_name": kpi_name,
            "value": round(value, 4),
            "trend_7d": round(trend_7d, 2) if trend_7d is not None else None,
            "trend_30d": round(trend_30d, 2) if trend_30d is not None else None,
            "computed_for_date": for_date.isoformat(),
        }, on_conflict="agent_name,kpi_name,computed_for_date").execute()
    try:
        await asyncio.to_thread(_up)
    except Exception:
        logger.exception("upsert_kpi failed for %s/%s", agent_name, kpi_name)


async def _trends(agent_name: str, kpi_name: str, current: float) -> tuple[float | None, float | None]:
    """Return (trend_7d, trend_30d) as percentage deltas vs value on that prior date."""
    def _lookup(days_ago: int) -> float | None:
        sync = Database()
        target = (date.today() - timedelta(days=days_ago)).isoformat()
        res = (sync._client.table("agent_kpis")
               .select("value")
               .eq("agent_name", agent_name)
               .eq("kpi_name", kpi_name)
               .lte("computed_for_date", target)
               .order("computed_for_date", desc=True)
               .limit(1)
               .execute())
        rows = res.data or []
        return float(rows[0]["value"]) if rows else None

    try:
        prior_7 = await asyncio.to_thread(_lookup, 7)
        prior_30 = await asyncio.to_thread(_lookup, 30)
    except Exception:
        logger.exception("trend lookup failed")
        return None, None

    def _pct(now: float, prior: float | None) -> float | None:
        if prior is None or prior == 0:
            return None
        return ((now - prior) / prior) * 100.0

    return _pct(current, prior_7), _pct(current, prior_30)


async def latest_kpi(agent_name: str) -> dict[str, Any] | None:
    """Most recent KPI row for an agent — used on the dashboard."""
    def _q():
        sync = Database()
        return (sync._client.table("agent_kpis")
                .select("*")
                .eq("agent_name", agent_name)
                .order("computed_for_date", desc=True)
                .limit(1)
                .execute())
    try:
        res = await asyncio.to_thread(_q)
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        logger.exception("latest_kpi read failed for %s", agent_name)
        return None


async def kpi_history(agent_name: str, days: int = 30) -> list[dict[str, Any]]:
    """Last N days of KPIs for sparkline rendering."""
    since = (date.today() - timedelta(days=days)).isoformat()

    def _q():
        sync = Database()
        return (sync._client.table("agent_kpis")
                .select("*")
                .eq("agent_name", agent_name)
                .gte("computed_for_date", since)
                .order("computed_for_date")
                .execute())
    try:
        res = await asyncio.to_thread(_q)
        return res.data or []
    except Exception:
        logger.exception("kpi_history read failed for %s", agent_name)
        return []
