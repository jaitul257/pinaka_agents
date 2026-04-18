"""RFM segmentation + 365-day LTV projection (Phase 10.B).

Classic RFM with absolute thresholds (not quintiles) because our customer
count is small enough that quintiles would be unstable. A "champion" here
is a real champion; not "champion relative to last week's customers."

Runs daily. One row per customer per day in `customer_rfm`.

Segment ladder (first match wins):
  - champion:      R≥4 + F≥3 + M≥3      active + repeat + high-value
  - loyal:         F≥3 + M≥3            repeat + high-value (any recency)
  - at_risk:       R≤2 + F≥2            used to buy, slipping
  - hibernating:   R≤2 + F=1            bought once, long ago
  - new:           R=5 + F=1            just bought, first time
  - one_and_done:  F=1 + R≤3            bought once, getting old
  - lost:          fallback             everyone else
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.core.database import AsyncDatabase

logger = logging.getLogger(__name__)


# Absolute thresholds (keep in code — business rules belong in diff)
R_THRESHOLDS = [30, 90, 180, 365]     # days since last order; ≤30=5, ≤90=4, ≤180=3, ≤365=2, >365=1
F_THRESHOLDS = [1, 2, 4, 9]           # order count; 1=1, 2=2, 3-4=3, 5-9=4, 10+=5
M_THRESHOLDS = [5_000, 10_000, 20_000, 50_000]  # $ total; <=5K=1..

# LTV projection: average order × projected orders in next 12 months.
# Projected orders scales with historical frequency, capped at a realistic
# number for $5K AOV fine jewelry (most buyers = 1-3 pieces/lifetime).
PROJECTED_ORDERS_MAP = {1: 0.3, 2: 0.7, 3: 1.2, 4: 2.0, 5: 3.0}


@dataclass
class RFMResult:
    customer_id: int
    computed_date: date
    recency_days: int | None
    frequency: int
    monetary: float
    r_score: int
    f_score: int
    m_score: int
    rfm_score_total: int
    segment: str
    avg_order_value: float
    projected_ltv_365d: float


class RFMScorer:
    """Compute + persist RFM scores for every customer with at least one paid order."""

    def __init__(self):
        self._db = AsyncDatabase()

    async def run_daily(self) -> dict[str, Any]:
        customers_with_orders = await self._load_buyers()
        today = date.today()
        results: list[RFMResult] = []

        for row in customers_with_orders:
            customer_id = row["id"]
            orders = row.get("_orders", [])
            if not orders:
                continue

            scored = self._score_one(customer_id, orders, today)
            await self._persist(scored)
            await self._update_customer_pointer(customer_id, scored.segment)
            results.append(scored)

        segment_counts: dict[str, int] = {}
        for r in results:
            segment_counts[r.segment] = segment_counts.get(r.segment, 0) + 1

        logger.info(
            "RFM: scored %d customers. Segments: %s",
            len(results), segment_counts,
        )
        return {
            "scored": len(results),
            "segment_counts": segment_counts,
            "computed_date": today.isoformat(),
        }

    async def _load_buyers(self) -> list[dict[str, Any]]:
        """All customers with at least one paid order. Returns list of
        customer rows each with an extra `_orders` list of their paid orders."""
        client = self._db._sync._client
        import asyncio

        # Fetch all paid + delivered orders with customer_id set
        orders = await asyncio.to_thread(
            lambda: (
                client.table("orders")
                .select("id,customer_id,total,refund_amount,created_at,status")
                .neq("customer_id", None)
                .in_("status", ["paid", "fulfilled", "delivered"])
                .execute()
            )
        )

        # Group by customer_id
        by_customer: dict[int, list[dict[str, Any]]] = {}
        for o in orders.data or []:
            cid = o.get("customer_id")
            if cid is None:
                continue
            by_customer.setdefault(int(cid), []).append(o)

        if not by_customer:
            return []

        # Fetch customers in one shot
        customers_resp = await asyncio.to_thread(
            lambda: (
                client.table("customers")
                .select("id, email, name")
                .in_("id", list(by_customer.keys()))
                .execute()
            )
        )
        out = []
        for c in customers_resp.data or []:
            c["_orders"] = by_customer.get(int(c["id"]), [])
            out.append(c)
        return out

    def _score_one(
        self, customer_id: int, orders: list[dict[str, Any]], today: date
    ) -> RFMResult:
        """Given all of a customer's paid orders, compute R, F, M, segment, LTV."""
        # Net total per order = total - refund_amount, floored at 0
        net_totals = [
            max(float(o.get("total") or 0) - float(o.get("refund_amount") or 0), 0)
            for o in orders
        ]
        monetary = round(sum(net_totals), 2)
        frequency = len(orders)
        avg_order_value = round(monetary / frequency, 2) if frequency else 0.0

        # Recency from most-recent order
        dates = [_parse_date(o.get("created_at")) for o in orders]
        dates = [d for d in dates if d is not None]
        recency_days: int | None = None
        if dates:
            recency_days = (today - max(dates)).days

        r = _bucket(recency_days if recency_days is not None else 10_000, R_THRESHOLDS, inverted=True)
        f = _bucket(frequency, F_THRESHOLDS)
        m = _bucket(monetary, M_THRESHOLDS)
        segment = _segment_of(r, f, m)

        # LTV = avg_order × projected_orders_next_12mo
        projected = PROJECTED_ORDERS_MAP.get(f, 0.3)
        # Bonus multiplier if recency is hot (reopens frequency bump)
        if r >= 4:
            projected *= 1.3
        projected_ltv = round(avg_order_value * projected, 2)

        return RFMResult(
            customer_id=customer_id,
            computed_date=today,
            recency_days=recency_days,
            frequency=frequency,
            monetary=monetary,
            r_score=r, f_score=f, m_score=m,
            rfm_score_total=r + f + m,
            segment=segment,
            avg_order_value=avg_order_value,
            projected_ltv_365d=projected_ltv,
        )

    async def _persist(self, r: RFMResult) -> None:
        row = {
            "customer_id": r.customer_id,
            "computed_date": r.computed_date.isoformat(),
            "recency_days": r.recency_days,
            "frequency": r.frequency,
            "monetary": r.monetary,
            "r_score": r.r_score,
            "f_score": r.f_score,
            "m_score": r.m_score,
            "rfm_score_total": r.rfm_score_total,
            "segment": r.segment,
            "avg_order_value": r.avg_order_value,
            "projected_ltv_365d": r.projected_ltv_365d,
        }
        client = self._db._sync._client
        import asyncio
        await asyncio.to_thread(
            lambda: (
                client.table("customer_rfm")
                .upsert(row, on_conflict="customer_id,computed_date")
                .execute()
            )
        )

    async def _update_customer_pointer(self, customer_id: int, segment: str) -> None:
        client = self._db._sync._client
        import asyncio
        await asyncio.to_thread(
            lambda: (
                client.table("customers")
                .update({
                    "last_rfm_at": datetime.now(timezone.utc).isoformat(),
                    "last_segment": segment,
                })
                .eq("id", customer_id)
                .execute()
            )
        )

    async def get_segment_counts(self) -> dict[str, int]:
        """Current distribution across segments. Used by the dashboard brief."""
        client = self._db._sync._client
        import asyncio
        resp = await asyncio.to_thread(
            lambda: (
                client.table("customers")
                .select("last_segment")
                .neq("last_segment", None)
                .execute()
            )
        )
        counts: dict[str, int] = {}
        for row in resp.data or []:
            seg = row.get("last_segment") or "unknown"
            counts[seg] = counts.get(seg, 0) + 1
        return counts


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        if isinstance(value, date):
            return value
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _bucket(value: float | int, thresholds: list[int | float], inverted: bool = False) -> int:
    """Map a value to 1-5 based on a 4-element threshold list.

    For recency (lower = better), pass inverted=True so ≤30=5, >365=1.
    For frequency/monetary (higher = better), the default mapping:
      ≤T1 → 1, ≤T2 → 2, ≤T3 → 3, ≤T4 → 4, >T4 → 5.
    """
    v = float(value)
    if inverted:
        # Lower is better
        if v <= thresholds[0]:
            return 5
        if v <= thresholds[1]:
            return 4
        if v <= thresholds[2]:
            return 3
        if v <= thresholds[3]:
            return 2
        return 1
    else:
        if v <= thresholds[0]:
            return 1
        if v <= thresholds[1]:
            return 2
        if v <= thresholds[2]:
            return 3
        if v <= thresholds[3]:
            return 4
        return 5


def _segment_of(r: int, f: int, m: int) -> str:
    """First-match-wins segment ladder. Keep order opinionated.

    Design note: `at_risk` wins over `loyal` when recency is low. A repeat
    buyer who stopped coming is a MORE actionable signal than a repeat buyer
    who's still around. Reverse that ordering and the best interventions
    (win-back email, personal call) never fire.
    """
    if r >= 4 and f >= 3 and m >= 3:
        return "champion"
    if r <= 2 and f >= 2:
        return "at_risk"              # Repeat buyer who's fading — flag before they churn
    if f >= 3 and m >= 3:
        return "loyal"
    if r == 5 and f == 1:
        return "new"
    if f == 1 and r <= 2:
        return "hibernating"          # One purchase, long gone
    if f == 1 and r == 3:
        return "one_and_done"         # One purchase, aging out
    return "active"                   # Neutral fallback
