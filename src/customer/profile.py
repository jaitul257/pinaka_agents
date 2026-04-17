"""Unified customer profile (Phase 10.A).

One dataclass aggregating everything the system knows about a customer:
  - Identity: id, email, name, phone, lifecycle_stage
  - Money: orders list, total_spent, avg_order, last_order_date
  - Latest RFM: segment, scores, projected LTV
  - Signals: messages count, recent chat sessions, survey responses
  - Context: captured anniversaries, welcome step, lifecycle emails sent

Used by:
  - /api/customer/{id}/profile endpoint (founder lookup, concierge personalization)
  - Lifecycle orchestrator (condition-based triggers in future phase)
  - Dashboard brief (segment counts + hot profiles)

Read-only. Pure aggregation. Zero mutations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.core.database import AsyncDatabase

logger = logging.getLogger(__name__)


@dataclass
class OrderSummary:
    shopify_order_id: str
    total: float
    refund_amount: float
    net: float
    status: str
    created_at: str
    line_items: list[str]


@dataclass
class RFMSnapshot:
    computed_date: str
    r_score: int
    f_score: int
    m_score: int
    rfm_score_total: int
    segment: str
    recency_days: int | None
    frequency: int
    monetary: float
    avg_order_value: float
    projected_ltv_365d: float


@dataclass
class CustomerProfile:
    # Identity
    customer_id: int
    shopify_customer_id: int | None
    email: str
    name: str
    phone: str
    lifecycle_stage: str
    accepts_marketing: bool
    created_at: str | None

    # Money
    orders: list[OrderSummary] = field(default_factory=list)
    total_spent: float = 0.0
    net_spent: float = 0.0
    order_count: int = 0
    avg_order_value: float = 0.0
    last_order_date: str | None = None

    # Latest RFM snapshot
    rfm: RFMSnapshot | None = None

    # Signals
    message_count: int = 0
    last_message_at: str | None = None
    survey_responses: list[dict[str, Any]] = field(default_factory=list)
    anniversaries: list[dict[str, Any]] = field(default_factory=list)

    # Pipeline state
    welcome_step: int = 0
    welcome_started_at: str | None = None
    lifecycle_emails_sent: dict[str, str] = field(default_factory=dict)

    # Meta
    generated_at: str = ""


class CustomerProfileBuilder:
    """Aggregate one customer's full profile from Supabase."""

    def __init__(self):
        self._db = AsyncDatabase()

    async def for_customer(self, customer_id: int) -> CustomerProfile | None:
        client = self._db._sync._client
        import asyncio

        # 1. Base customer row
        c_resp = await asyncio.to_thread(
            lambda: client.table("customers").select("*").eq("id", customer_id).execute()
        )
        if not c_resp.data:
            return None
        c = c_resp.data[0]

        profile = CustomerProfile(
            customer_id=customer_id,
            shopify_customer_id=c.get("shopify_customer_id"),
            email=c.get("email") or "",
            name=c.get("name") or "",
            phone=c.get("phone") or "",
            lifecycle_stage=c.get("lifecycle_stage") or "unknown",
            accepts_marketing=bool(c.get("accepts_marketing")),
            created_at=c.get("created_at"),
            welcome_step=int(c.get("welcome_step") or 0),
            welcome_started_at=c.get("welcome_started_at"),
            lifecycle_emails_sent=c.get("lifecycle_emails_sent") or {},
            generated_at=datetime.utcnow().isoformat(),
        )

        # 2. Orders
        o_resp = await asyncio.to_thread(
            lambda: (
                client.table("orders")
                .select("shopify_order_id,total,refund_amount,status,created_at,line_items_json")
                .eq("customer_id", customer_id)
                .order("created_at", desc=True)
                .execute()
            )
        )
        for o in o_resp.data or []:
            total = float(o.get("total") or 0)
            refund = float(o.get("refund_amount") or 0)
            net = max(total - refund, 0)
            items_raw = o.get("line_items_json") or "[]"
            try:
                import json as _json
                items = _json.loads(items_raw) if isinstance(items_raw, str) else items_raw
                line_items = [li.get("title", "Item") for li in items if isinstance(li, dict)]
            except Exception:
                line_items = []
            profile.orders.append(OrderSummary(
                shopify_order_id=str(o.get("shopify_order_id") or ""),
                total=total, refund_amount=refund, net=net,
                status=o.get("status") or "",
                created_at=o.get("created_at") or "",
                line_items=line_items,
            ))
        profile.order_count = len(profile.orders)
        profile.total_spent = round(sum(o.total for o in profile.orders), 2)
        profile.net_spent = round(sum(o.net for o in profile.orders), 2)
        profile.avg_order_value = round(
            profile.net_spent / profile.order_count, 2
        ) if profile.order_count else 0.0
        profile.last_order_date = profile.orders[0].created_at if profile.orders else None

        # 3. Latest RFM
        rfm_resp = await asyncio.to_thread(
            lambda: (
                client.table("customer_rfm")
                .select("*")
                .eq("customer_id", customer_id)
                .order("computed_date", desc=True)
                .limit(1)
                .execute()
            )
        )
        if rfm_resp.data:
            r = rfm_resp.data[0]
            profile.rfm = RFMSnapshot(
                computed_date=str(r.get("computed_date") or ""),
                r_score=int(r.get("r_score") or 0),
                f_score=int(r.get("f_score") or 0),
                m_score=int(r.get("m_score") or 0),
                rfm_score_total=int(r.get("rfm_score_total") or 0),
                segment=r.get("segment") or "unknown",
                recency_days=r.get("recency_days"),
                frequency=int(r.get("frequency") or 0),
                monetary=float(r.get("monetary") or 0),
                avg_order_value=float(r.get("avg_order_value") or 0),
                projected_ltv_365d=float(r.get("projected_ltv_365d") or 0),
            )

        # 4. Messages (email support threads, best-effort)
        if profile.email:
            try:
                m_resp = await asyncio.to_thread(
                    lambda: (
                        client.table("messages")
                        .select("created_at", count="exact")
                        .eq("buyer_email", profile.email)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                )
                profile.message_count = int(m_resp.count or 0)
                if m_resp.data:
                    profile.last_message_at = m_resp.data[0].get("created_at")
            except Exception:
                logger.exception("Profile: messages query failed (non-fatal)")

        # 5. Post-purchase survey responses
        order_ids = [o.shopify_order_id for o in profile.orders]
        if order_ids:
            try:
                a_resp = await asyncio.to_thread(
                    lambda: (
                        client.table("post_purchase_attribution")
                        .select("shopify_order_id,channel_primary,channel_detail,purchase_reason,anniversary_date,relationship,created_at")
                        .in_("shopify_order_id", order_ids)
                        .execute()
                    )
                )
                profile.survey_responses = a_resp.data or []
            except Exception:
                logger.exception("Profile: survey query failed (non-fatal)")

        # 6. Captured anniversaries
        try:
            anniv_resp = await asyncio.to_thread(
                lambda: (
                    client.table("customer_anniversaries")
                    .select("anniversary_date,relationship,notes")
                    .eq("customer_id", customer_id)
                    .execute()
                )
            )
            profile.anniversaries = anniv_resp.data or []
        except Exception:
            logger.exception("Profile: anniversaries query failed (non-fatal)")

        return profile

    def to_json(self, profile: CustomerProfile) -> dict[str, Any]:
        """Convert dataclass tree → plain dict for JSON response."""
        return {
            "customer_id": profile.customer_id,
            "shopify_customer_id": profile.shopify_customer_id,
            "email": profile.email,
            "name": profile.name,
            "phone": profile.phone,
            "lifecycle_stage": profile.lifecycle_stage,
            "accepts_marketing": profile.accepts_marketing,
            "created_at": profile.created_at,
            "money": {
                "order_count": profile.order_count,
                "total_spent": profile.total_spent,
                "net_spent": profile.net_spent,
                "avg_order_value": profile.avg_order_value,
                "last_order_date": profile.last_order_date,
            },
            "orders": [
                {
                    "shopify_order_id": o.shopify_order_id,
                    "total": o.total, "refund_amount": o.refund_amount, "net": o.net,
                    "status": o.status, "created_at": o.created_at,
                    "line_items": o.line_items,
                }
                for o in profile.orders
            ],
            "rfm": {
                "segment": profile.rfm.segment,
                "r_score": profile.rfm.r_score, "f_score": profile.rfm.f_score,
                "m_score": profile.rfm.m_score, "rfm_score_total": profile.rfm.rfm_score_total,
                "recency_days": profile.rfm.recency_days,
                "frequency": profile.rfm.frequency,
                "monetary": profile.rfm.monetary,
                "avg_order_value": profile.rfm.avg_order_value,
                "projected_ltv_365d": profile.rfm.projected_ltv_365d,
                "computed_date": profile.rfm.computed_date,
            } if profile.rfm else None,
            "signals": {
                "message_count": profile.message_count,
                "last_message_at": profile.last_message_at,
                "survey_responses": profile.survey_responses,
                "anniversaries": profile.anniversaries,
            },
            "pipeline": {
                "welcome_step": profile.welcome_step,
                "welcome_started_at": profile.welcome_started_at,
                "lifecycle_emails_sent": profile.lifecycle_emails_sent,
            },
            "generated_at": profile.generated_at,
        }
