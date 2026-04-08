"""Observation writer — the agent awareness layer.

Every significant business event gets written as a human-readable observation.
The heartbeat scans these to find things that need attention.

Usage:
    from src.agents.observations import observe

    await observe(
        source="webhook:order",
        category="order",
        severity="info",
        summary="New order #1234 from Sarah ($9,998) — Yellow Gold, 7 inch",
        entity_type="order",
        entity_id="1234",
        data={"total": 9998, "customer": "sarah@example.com"},
    )
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from src.core.database import Database

logger = logging.getLogger(__name__)

_db = None


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


async def observe(
    source: str,
    category: str,
    summary: str,
    severity: str = "info",
    entity_type: str | None = None,
    entity_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Write a business observation. Non-blocking, never raises."""
    try:
        row = {
            "source": source,
            "category": category,
            "severity": severity,
            "summary": summary,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "data": data or {},
        }
        await asyncio.to_thread(
            lambda: _get_db()._client.table("observations").insert(row).execute()
        )
    except Exception:
        logger.exception("Failed to write observation: %s", summary[:100])


# ── Pre-built observation helpers for common events ──


async def observe_new_order(order_data: dict, customer_name: str, total: float) -> None:
    order_id = order_data.get("id") or order_data.get("shopify_order_id")
    items = [li.get("title", "Item") for li in order_data.get("line_items", [])]
    await observe(
        source="webhook:order",
        category="order",
        severity="info",
        summary=f"New order #{order_id} from {customer_name} (${total:,.2f}) — {', '.join(items[:3])}",
        entity_type="order",
        entity_id=str(order_id),
        data={"total": total, "customer": customer_name, "items": items},
    )


async def observe_fraud_flag(order_id: int, reasons: list[str], total: float) -> None:
    await observe(
        source="webhook:order",
        category="order",
        severity="critical",
        summary=f"FRAUD FLAGGED: Order #{order_id} (${total:,.2f}) — {'; '.join(reasons)}",
        entity_type="order",
        entity_id=str(order_id),
        data={"reasons": reasons, "total": total},
    )


async def observe_customer_message(email: str, category: str, is_urgent: bool) -> None:
    severity = "warning" if is_urgent else "info"
    await observe(
        source="webhook:email",
        category="support",
        severity=severity,
        summary=f"{'URGENT ' if is_urgent else ''}Customer message from {email} — category: {category}",
        entity_type="customer",
        entity_id=email,
        data={"category": category, "urgent": is_urgent},
    )


async def observe_shipping_update(order_id: int, status: str, tracking: str = "") -> None:
    await observe(
        source="webhook:shipstation",
        category="shipping",
        severity="info",
        summary=f"Order #{order_id} shipping update: {status}" + (f" (tracking: {tracking})" if tracking else ""),
        entity_type="order",
        entity_id=str(order_id),
        data={"status": status, "tracking": tracking},
    )


async def observe_roas_change(roas: float, spend: float, revenue: float) -> None:
    severity = "critical" if roas < 2.0 else ("warning" if roas < 4.0 else "info")
    await observe(
        source="cron:roas",
        category="marketing",
        severity=severity,
        summary=f"ROAS: {roas:.1f}x (spend: ${spend:,.2f}, revenue: ${revenue:,.2f})",
        entity_type="metric",
        entity_id="roas",
        data={"roas": roas, "spend": spend, "revenue": revenue},
    )


async def observe_cart_abandoned(email: str, cart_value: float, items: list[str]) -> None:
    severity = "warning" if cart_value > 5000 else "info"
    await observe(
        source="webhook:checkout",
        category="customer",
        severity=severity,
        summary=f"Cart abandoned: {email} — ${cart_value:,.2f} ({', '.join(items[:2])})",
        entity_type="customer",
        entity_id=email,
        data={"cart_value": cart_value, "items": items},
    )


async def observe_agent_action(agent_name: str, action: str, details: str = "") -> None:
    await observe(
        source=f"agent:{agent_name}",
        category="system",
        severity="info",
        summary=f"Agent {agent_name}: {action}" + (f" — {details}" if details else ""),
        entity_type="agent",
        entity_id=agent_name,
        data={"action": action, "details": details},
    )
