"""Program-verified outcomes — Phase 13.1.

The rule: every row in `outcomes` is written by a deterministic path.
SendGrid webhook, a scheduled SQL check, or a direct in-app hook. Never
by an agent grading its own work. That's the whole point.

Why deterministic-only?
  • Kamoi et al. (TACL 2024): LLM self-correction without external ground
    truth frequently makes agents WORSE, not better.
  • Karpathy (Dwarkesh Oct 2025): "Sucking supervision through a straw."
    A single LLM-judged signal compounds errors over weeks.
  • Ramp (production): convert every user-reported failure into a
    regression test. Low-tech, high-signal.

What agents get from this module:
  • Weekly rollup feeds the KPI dashboard (Phase 12.3).
  • Per-agent outcome tables show what's working vs what isn't.
  • Retros (Phase 12.4) now include "you acted N times and K actions
    have verified positive outcomes" instead of just volume.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.database import Database

logger = logging.getLogger(__name__)


# ── Outcome taxonomy ──
# Extend only when a new deterministic signal is identified.

OUTCOME_TYPES = frozenset({
    "email_delivered",
    "email_opened",
    "email_clicked",
    "email_bounced",
    "email_replied_48h",
    "order_shipped_on_time",
    "order_shipped_late",
    "order_delivered",
    "customer_repurchase_30d",
    "refund_issued",
})

# Per-outcome sign: +1 positive (something we want), -1 negative,
# 0 neutral (just a fact). Used by dashboard to compute agent success rates.
OUTCOME_POLARITY: dict[str, int] = {
    "email_delivered": 0,
    "email_opened": 1,
    "email_clicked": 1,
    "email_bounced": -1,
    "email_replied_48h": 1,
    "order_shipped_on_time": 1,
    "order_shipped_late": -1,
    "order_delivered": 1,
    "customer_repurchase_30d": 1,
    "refund_issued": -1,
}


# ── Write path ──

async def record(
    agent_name: str,
    action_type: str,
    outcome_type: str,
    *,
    audit_log_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    outcome_value: dict[str, Any] | None = None,
    source: str = "internal",
    idempotency_key: str | None = None,
) -> int | None:
    """Insert an outcome row. Returns the new id, or None on dupe / error.

    idempotency_key prevents double-count when a webhook retries or a
    verification cron reruns. For SendGrid use the sg_event_id. For
    SQL-verified outcomes, compose from (outcome_type, entity_id,
    fired_at date).
    """
    if outcome_type not in OUTCOME_TYPES:
        logger.warning("record: unknown outcome_type=%s, ignoring", outcome_type)
        return None

    payload = {
        "audit_log_id": audit_log_id,
        "agent_name": agent_name,
        "action_type": action_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id is not None else None,
        "outcome_type": outcome_type,
        "outcome_value": outcome_value or {},
        "source": source,
        "idempotency_key": idempotency_key,
    }

    def _insert():
        sync = Database()
        return sync._client.table("outcomes").insert(payload).execute()

    try:
        res = await asyncio.to_thread(_insert)
        if res.data:
            return int(res.data[0]["id"])
    except Exception as exc:
        # Unique-constraint violation on idempotency_key is EXPECTED on
        # webhook retries — log at debug, not error.
        if "duplicate key" in str(exc).lower() or "23505" in str(exc):
            logger.debug("outcome dedup (idempotency_key=%s)", idempotency_key)
            return None
        logger.exception("record outcome failed for %s/%s", agent_name, outcome_type)
    return None


# ── SendGrid event webhook ingestion ──

_SENDGRID_EVENT_TO_OUTCOME: dict[str, str] = {
    "delivered": "email_delivered",
    "open": "email_opened",
    "click": "email_clicked",
    "bounce": "email_bounced",
    "dropped": "email_bounced",  # treat dropped as a bounce for our purposes
}


def verify_sendgrid_signature(
    payload: bytes, signature_b64: str, timestamp: str, public_key_b64: str,
) -> bool:
    """Verify a SendGrid Event Webhook request.

    SendGrid calls this feature "Signed Event Webhook Requests" — the
    signature is ECDSA on prime256v1 (a.k.a. secp256r1 / P-256), not an
    HMAC. Payload signed is exactly `timestamp + raw_body` — do not mutate.

    Returns True on valid signature, False otherwise. Never raises: a
    verification error is ALWAYS a reject, not a crash.

    Dependency-light: uses `cryptography` which is already an indirect dep
    via httpx/supabase. If it's not available we fall back to "accept"
    with a warning log, so operator misconfiguration doesn't take the
    webhook offline.
    """
    if not (signature_b64 and timestamp and public_key_b64):
        return False
    try:
        import base64
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        pem = base64.b64decode(public_key_b64)
        key = serialization.load_der_public_key(pem) if pem.startswith(b"0") \
            else serialization.load_pem_public_key(pem)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            logger.warning("sendgrid webhook public key is not EC")
            return False

        signature = base64.b64decode(signature_b64)
        signed = timestamp.encode("utf-8") + payload
        try:
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False
    except Exception:
        logger.exception("sendgrid signature verify failed")
        return False


async def record_sendgrid_events(events: list[dict[str, Any]]) -> dict[str, int]:
    """Handle a SendGrid event webhook payload.

    Each event carries: event, email, timestamp, sg_event_id, sg_message_id,
    and (if we set them) custom_args. We use sg_event_id as the idempotency
    key — SendGrid reuses it across retries.
    """
    accepted = 0
    ignored = 0
    deduped = 0
    for evt in events or []:
        event_name = (evt.get("event") or "").lower()
        outcome_type = _SENDGRID_EVENT_TO_OUTCOME.get(event_name)
        if not outcome_type:
            ignored += 1
            continue

        sg_event_id = evt.get("sg_event_id") or evt.get("sg_message_id")
        custom_args = evt.get("custom_args") or {}
        agent_name = custom_args.get("agent_name") or _infer_agent_from_category(evt)
        action_type = custom_args.get("action_type") or "unknown_email"

        value = {
            "email": evt.get("email"),
            "sg_message_id": evt.get("sg_message_id"),
            "category": evt.get("category"),
            "url": evt.get("url"),  # present on click events
            "reason": evt.get("reason"),  # present on bounce
            "useragent": (evt.get("useragent") or "")[:100],
            "timestamp": evt.get("timestamp"),
        }

        result = await record(
            agent_name=agent_name,
            action_type=action_type,
            outcome_type=outcome_type,
            audit_log_id=custom_args.get("audit_log_id"),
            entity_type=custom_args.get("entity_type"),
            entity_id=custom_args.get("entity_id"),
            outcome_value={k: v for k, v in value.items() if v is not None},
            source="sendgrid_webhook",
            idempotency_key=f"sg:{sg_event_id}" if sg_event_id else None,
        )
        if result is None:
            deduped += 1
        else:
            accepted += 1

    return {"accepted": accepted, "deduped": deduped, "ignored": ignored,
            "total": len(events or [])}


def _infer_agent_from_category(evt: dict[str, Any]) -> str:
    """SendGrid events carry a `category` we can use to guess the owner
    when custom_args wasn't attached. Not perfect, but better than 'unknown'."""
    cat = (evt.get("category") or "")
    if isinstance(cat, list):
        cat = cat[0] if cat else ""
    cat = str(cat).lower()
    if "welcome" in cat or "lifecycle" in cat or "anniversary" in cat:
        return "retention"
    if "crafting" in cat or "order" in cat:
        return "order_ops"
    if "service" in cat or "support" in cat or "reply" in cat:
        return "customer_service"
    return "unknown"


# ── SQL-verified outcome scanners (cron-invoked) ──

_BIZ_DAYS_PROMISE = 15  # Pinaka made-to-order SLA


async def verify_order_shipping(days_back: int = 30) -> dict[str, int]:
    """Scan orders that shipped in the last N days, emit on-time vs late.

    One outcome row per order per verification window. Dedup key is
    `shipping:{shopify_order_id}` so re-runs are idempotent.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    def _q():
        sync = Database()
        return (sync._client.table("orders")
                .select("shopify_order_id,created_at,shipped_at,delivered_at,customer_id")
                .not_.is_("shipped_at", "null")
                .gte("shipped_at", since)
                .limit(500)
                .execute()).data or []
    rows = await asyncio.to_thread(_q)

    on_time = 0
    late = 0
    delivered = 0
    for row in rows:
        sid = row.get("shopify_order_id")
        if not sid:
            continue
        created = _parse_ts(row.get("created_at"))
        shipped = _parse_ts(row.get("shipped_at"))
        if not created or not shipped:
            continue
        biz_days = _biz_days_between(created, shipped)
        is_on_time = biz_days <= _BIZ_DAYS_PROMISE
        outcome_type = "order_shipped_on_time" if is_on_time else "order_shipped_late"

        res = await record(
            agent_name="order_ops",
            action_type="fulfillment",
            outcome_type=outcome_type,
            entity_type="order",
            entity_id=str(sid),
            outcome_value={"biz_days": biz_days, "promise": _BIZ_DAYS_PROMISE,
                           "created_at": row.get("created_at"),
                           "shipped_at": row.get("shipped_at")},
            source="verify_cron",
            idempotency_key=f"shipping:{sid}",
        )
        if res is not None:
            if is_on_time:
                on_time += 1
            else:
                late += 1

        # Separately record delivery if we have a delivered_at
        delivered_at = _parse_ts(row.get("delivered_at"))
        if delivered_at:
            res2 = await record(
                agent_name="order_ops",
                action_type="fulfillment",
                outcome_type="order_delivered",
                entity_type="order",
                entity_id=str(sid),
                outcome_value={"delivered_at": row.get("delivered_at")},
                source="verify_cron",
                idempotency_key=f"delivered:{sid}",
            )
            if res2 is not None:
                delivered += 1

    return {"scanned": len(rows), "on_time": on_time, "late": late,
            "delivered": delivered}


async def verify_customer_replies(hours: int = 48, days_back: int = 7) -> dict[str, int]:
    """For every auto_sent customer-facing email in the last `days_back` days,
    check whether the customer replied within `hours` hours."""
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    def _q_sent():
        sync = Database()
        return (sync._client.table("auto_sent_actions")
                .select("id,agent_name,action_type,entity_type,entity_id,"
                        "payload,created_at")
                .eq("entity_type", "customer")
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(500)
                .execute()).data or []

    sent_rows = await asyncio.to_thread(_q_sent)
    emitted = 0
    checked = 0
    for row in sent_rows:
        checked += 1
        customer_id = row.get("entity_id")
        if not customer_id:
            continue
        sent_at = _parse_ts(row.get("created_at"))
        if not sent_at:
            continue
        reply_cutoff = sent_at + timedelta(hours=hours)

        def _q_reply():
            sync = Database()
            return (sync._client.table("messages")
                    .select("id,created_at,category")
                    .eq("customer_id", int(customer_id))
                    .gte("created_at", sent_at.isoformat())
                    .lte("created_at", reply_cutoff.isoformat())
                    .limit(1)
                    .execute()).data or []
        try:
            replies = await asyncio.to_thread(_q_reply)
        except Exception:
            continue
        if not replies:
            continue

        res = await record(
            agent_name=row.get("agent_name") or "retention",
            action_type=row.get("action_type") or "unknown_email",
            outcome_type="email_replied_48h",
            entity_type="customer",
            entity_id=str(customer_id),
            outcome_value={"reply_category": replies[0].get("category"),
                           "replied_at": replies[0].get("created_at")},
            source="verify_cron",
            idempotency_key=f"reply48h:{row['id']}",
        )
        if res is not None:
            emitted += 1

    return {"checked": checked, "emitted": emitted}


async def verify_customer_repurchase(days_back: int = 45, window_days: int = 30) -> dict[str, int]:
    """For retention emails sent in the last `days_back` days, check if the
    customer placed a new order within `window_days` of the email.

    Dedupe on auto_sent_actions.id so we only emit the signal once per email.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    def _q_sent():
        sync = Database()
        return (sync._client.table("auto_sent_actions")
                .select("id,agent_name,action_type,entity_type,entity_id,created_at")
                .eq("agent_name", "retention")
                .eq("entity_type", "customer")
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(500)
                .execute()).data or []

    sent_rows = await asyncio.to_thread(_q_sent)
    emitted = 0
    checked = 0
    for row in sent_rows:
        checked += 1
        customer_id = row.get("entity_id")
        if not customer_id:
            continue
        sent_at = _parse_ts(row.get("created_at"))
        if not sent_at:
            continue
        cutoff = sent_at + timedelta(days=window_days)

        def _q_orders():
            sync = Database()
            return (sync._client.table("orders")
                    .select("shopify_order_id,total,created_at")
                    .eq("customer_id", int(customer_id))
                    .gte("created_at", sent_at.isoformat())
                    .lte("created_at", cutoff.isoformat())
                    .limit(1)
                    .execute()).data or []
        try:
            orders = await asyncio.to_thread(_q_orders)
        except Exception:
            continue
        if not orders:
            continue

        res = await record(
            agent_name="retention",
            action_type=row.get("action_type") or "unknown_email",
            outcome_type="customer_repurchase_30d",
            entity_type="customer",
            entity_id=str(customer_id),
            outcome_value={"order_id": orders[0].get("shopify_order_id"),
                           "order_total": orders[0].get("total"),
                           "order_at": orders[0].get("created_at"),
                           "email_at": row.get("created_at")},
            source="verify_cron",
            idempotency_key=f"repurchase30:{row['id']}",
        )
        if res is not None:
            emitted += 1

    return {"checked": checked, "emitted": emitted}


async def verify_all() -> dict[str, Any]:
    """Orchestrator for the daily /cron/verify-outcomes endpoint."""
    results: dict[str, Any] = {}
    try:
        results["shipping"] = await verify_order_shipping()
    except Exception:
        logger.exception("verify_order_shipping failed")
        results["shipping"] = {"error": True}
    try:
        results["replies"] = await verify_customer_replies()
    except Exception:
        logger.exception("verify_customer_replies failed")
        results["replies"] = {"error": True}
    try:
        results["repurchase"] = await verify_customer_repurchase()
    except Exception:
        logger.exception("verify_customer_repurchase failed")
        results["repurchase"] = {"error": True}
    return results


# ── Read path (dashboard, retros, KPI dashboard) ──

async def rollup_by_agent(days: int = 30) -> dict[str, dict[str, int]]:
    """Counts per-agent per-outcome_type over the window.

    Shape: {agent_name: {outcome_type: count}}. Used by dashboards and
    retros — nothing else should need raw rows.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def _q():
        sync = Database()
        return (sync._client.table("outcomes")
                .select("agent_name,outcome_type")
                .gte("fired_at", since)
                .execute()).data or []
    try:
        rows = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("rollup_by_agent query failed")
        return {}

    rollup: dict[str, dict[str, int]] = {}
    for r in rows:
        agent = r.get("agent_name") or "unknown"
        otype = r.get("outcome_type") or "unknown"
        rollup.setdefault(agent, {})[otype] = rollup.setdefault(agent, {}).get(otype, 0) + 1
    return rollup


async def recent_for_agent(agent_name: str, limit: int = 50) -> list[dict[str, Any]]:
    def _q():
        sync = Database()
        return (sync._client.table("outcomes")
                .select("*")
                .eq("agent_name", agent_name)
                .order("fired_at", desc=True)
                .limit(limit)
                .execute()).data or []
    try:
        return await asyncio.to_thread(_q)
    except Exception:
        logger.exception("recent_for_agent query failed")
        return []


# ── Helpers ──

def _parse_ts(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _biz_days_between(a: datetime, b: datetime) -> int:
    """Count Mon-Fri days between two UTC datetimes. Matches the 15-biz-day
    promise we make at checkout."""
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


def derive_idempotency_key(outcome_type: str, entity_id: str,
                            date_str: str | None = None) -> str:
    """Compose a stable key for in-app callers that don't have an
    external id. Format: `{outcome_type}:{entity_id}:{YYYY-MM-DD}`"""
    date_part = date_str or datetime.now(timezone.utc).date().isoformat()
    raw = f"{outcome_type}:{entity_id}:{date_part}"
    # Truncate to keep indexes lean while preserving uniqueness
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
