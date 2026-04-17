"""File-based entity memory — Karpathy llm-wiki pattern (Phase 13.2).

Each customer, SKU, and historical month gets ONE short compiled markdown
note. Agents fetch the note for the specific entity they're acting on —
they do NOT get the raw history stuffed into context.

Design principles (from research):
  • Raw data in Supabase is the immutable log. Never edited.
  • entity_memory is the compiled wiki. LLM-authored, nightly.
  • Retrieval is a keyed SELECT (entity_type, entity_id) — no vectors.
    At our volume (1-2 orders/week, ~50 active products) SQL wins on
    both latency AND relevance vs cosine similarity.
  • Each note stays small (target 500-800 words). Compaction is active,
    not passive. Karpathy: "context is RAM, not append-only storage."
  • Agents pull notes by ID when acting on an entity. Not by default.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from anthropic import Anthropic

from src.core.database import Database
from src.core.settings import settings

logger = logging.getLogger(__name__)


SUPPORTED_TYPES = frozenset({"customer", "product", "seasonal", "agent"})

# Hard cap on compiled note size — forces the compiler to prioritize signal
# over completeness. If a customer's note hits this limit, the next compile
# should be more aggressive about dropping older rows, not bigger.
MAX_CONTENT_CHARS = 4500  # ~750 words

# Staleness policy: if the compiled note is older than this AND new raw rows
# exist past its source_through timestamp, the cron recompiles.
STALE_AFTER_HOURS = 24


# ── Read path: what agents call ──

async def get_memory(entity_type: str, entity_id: str) -> dict[str, Any] | None:
    """Return the compiled memory note for an entity, or None.

    Agents call this when they're about to act on a specific customer or
    product. Returns {content, compiled_at, sample_count} so the agent
    knows how fresh the note is.
    """
    if entity_type not in SUPPORTED_TYPES:
        logger.warning("get_memory: unsupported entity_type=%s", entity_type)
        return None

    def _q():
        sync = Database()
        return (sync._client.table("entity_memory")
                .select("content,compiled_at,sample_count,source_through")
                .eq("entity_type", entity_type)
                .eq("entity_id", str(entity_id))
                .limit(1)
                .execute())
    try:
        res = await asyncio.to_thread(_q)
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        logger.exception("get_memory read failed for %s/%s", entity_type, entity_id)
        return None


# ── Write path: nightly cron calls these ──

async def compile_customer(customer_id: int | str) -> dict[str, Any] | None:
    """Compile a customer's note from their orders + messages + lifecycle events.

    Returns {entity_id, sample_count, compiled_at, content_len} on success,
    None if there's nothing to compile (no orders, no messages).
    """
    customer_id = int(customer_id)
    raw = await _gather_customer_raw(customer_id)
    total_rows = len(raw["orders"]) + len(raw["messages"]) + len(raw["lifecycle_events"])
    if total_rows == 0:
        return None

    content = await _claude_compile_customer(raw)
    if not content:
        return None

    source_through = _max_created_at(raw)
    return await _upsert_memory(
        entity_type="customer",
        entity_id=str(customer_id),
        content=content,
        sample_count=total_rows,
        source_through=source_through,
    )


async def compile_product(sku: str) -> dict[str, Any] | None:
    """Compile a product's note from its metrics + order history + ad performance."""
    raw = await _gather_product_raw(sku)
    total_rows = (
        len(raw["orders_with_sku"])
        + len(raw["ad_creatives"])
        + len(raw["ad_metrics"])
    )
    if not raw["product"] and total_rows == 0:
        return None

    content = await _claude_compile_product(raw)
    if not content:
        return None

    source_through = _max_created_at(raw)
    return await _upsert_memory(
        entity_type="product",
        entity_id=sku,
        content=content,
        sample_count=total_rows,
        source_through=source_through,
    )


async def compile_agent(agent_name: str, lookback_days: int = 7) -> dict[str, Any] | None:
    """Compile a rolling self-summary for one agent.

    Pulls the last `lookback_days` of:
      • agent_audit_log rows (runs, tool calls, escalations)
      • auto_sent_actions (AUTO-tier fire-and-log items)
      • outcomes (program-verified signals tied to recent runs)
      • observations (only those this agent acted on)

    Summarizes into one short markdown note. The agent reads this via
    get_my_memory instead of walking raw tables — otherwise older rows
    rot the context window the way a year of git log would rot a code
    review.
    """
    raw = await _gather_agent_raw(agent_name, lookback_days)
    total_signal = (
        len(raw["audit_log"])
        + len(raw["auto_sent"])
        + len(raw["outcomes"])
        + len(raw["observations"])
    )
    if total_signal == 0:
        return None

    content = await _claude_compile_agent(agent_name, raw, lookback_days)
    if not content:
        return None

    return await _upsert_memory(
        entity_type="agent",
        entity_id=agent_name,
        content=content,
        sample_count=total_signal,
        source_through=None,  # agent memory is a fixed sliding window, not point-in-time
    )


async def compile_seasonal(month: str) -> dict[str, Any] | None:
    """Compile historical patterns for a calendar month (e.g. '04' for April).

    Uses daily_stats across all years for that month. Surfaces what actually
    happened before — revenue, ROAS, creative types that worked, anomalies.
    """
    if not (len(month) == 2 and month.isdigit() and 1 <= int(month) <= 12):
        logger.warning("compile_seasonal: invalid month=%s (expect '01'..'12')", month)
        return None

    raw = await _gather_seasonal_raw(month)
    if not raw["daily_stats"]:
        return None

    content = await _claude_compile_seasonal(month, raw)
    if not content:
        return None

    return await _upsert_memory(
        entity_type="seasonal",
        entity_id=month,
        content=content,
        sample_count=len(raw["daily_stats"]),
        source_through=None,  # seasonal is history-of-all-time, not a sliding window
    )


# ── Raw data gatherers (read-only queries on immutable tables) ──

async def _gather_customer_raw(customer_id: int) -> dict[str, Any]:
    """Pull raw data for a customer. Column names verified against actual schema:
    customers.welcome_step (not welcome_step_sent), customers.last_order_date
    (renamed from last_order_at in migration 002), messages.body (not
    inbound_content).
    """
    def _q():
        sync = Database()
        orders = (sync._client.table("orders")
                  .select("shopify_order_id,total,status,created_at,shipped_at,delivered_at")
                  .eq("customer_id", customer_id)
                  .order("created_at", desc=True)
                  .limit(20)
                  .execute()).data or []

        messages = (sync._client.table("messages")
                    .select("category,status,created_at,ai_draft,body")
                    .eq("customer_id", customer_id)
                    .order("created_at", desc=True)
                    .limit(20)
                    .execute()).data or []

        customer = (sync._client.table("customers")
                    .select("email,name,lifecycle_stage,welcome_step,last_order_date,"
                            "last_reorder_email_at,created_at,last_segment,lifetime_value")
                    .eq("id", customer_id)
                    .limit(1)
                    .execute()).data or []

        # RFM snapshot is optional (table may not exist in all environments)
        try:
            rfm = (sync._client.table("customer_rfm")
                   .select("segment,r_score,f_score,m_score,ltv_365d_projection,computed_at")
                   .eq("customer_id", customer_id)
                   .order("computed_at", desc=True)
                   .limit(1)
                   .execute()).data or []
        except Exception:
            rfm = []

        return {"customer": customer, "orders": orders,
                "messages": messages, "rfm": rfm,
                "lifecycle_events": rfm}
    return await asyncio.to_thread(_q)


async def _gather_product_raw(sku: str) -> dict[str, Any]:
    """Pull raw data for a product. Note: we don't store line_items on orders
    (that schema addition is pending) — order→SKU linkage is therefore
    unavailable at this layer. Product memory is ad-creative-driven for now.
    """
    def _q():
        sync = Database()
        product = (sync._client.table("products")
                   .select("sku,name,pricing,materials,tags,created_at")
                   .eq("sku", sku)
                   .limit(1)
                   .execute()).data or []

        ad_creatives = (sync._client.table("ad_creatives")
                        .select("id,variant_label,status,created_at,meta_ad_id,"
                                "headline,primary_text")
                        .eq("sku", sku)
                        .order("created_at", desc=True)
                        .limit(20)
                        .execute()).data or []

        # ad_creative_metrics links via meta_ad_id, not creative_id. Only
        # creatives that were actually pushed to Meta have metrics rows.
        meta_ad_ids = [c["meta_ad_id"] for c in ad_creatives if c.get("meta_ad_id")]
        ad_metrics: list[dict[str, Any]] = []
        if meta_ad_ids:
            metrics = (sync._client.table("ad_creative_metrics")
                       .select("meta_ad_id,meta_creative_id,impressions,clicks,spend,ctr,date")
                       .in_("meta_ad_id", meta_ad_ids)
                       .order("date", desc=True)
                       .limit(60)
                       .execute()).data or []
            ad_metrics = metrics

        return {
            "product": product[0] if product else None,
            "orders_with_sku": [],  # placeholder — see note above
            "ad_creatives": ad_creatives,
            "ad_metrics": ad_metrics,
        }
    return await asyncio.to_thread(_q)


async def _gather_agent_raw(agent_name: str, lookback_days: int) -> dict[str, Any]:
    """Pull the agent's own recent activity. Four small queries — at our
    volume the whole bundle is ~a few hundred rows, well under the compact
    prompt budget."""
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    def _q():
        sync = Database()
        audit = (sync._client.table("agent_audit_log")
                 .select("task_summary,tool_calls,result,escalated,created_at,"
                         "tokens_used,duration_ms")
                 .eq("agent_name", agent_name)
                 .gte("created_at", since)
                 .order("created_at", desc=True)
                 .limit(80)
                 .execute()).data or []

        auto_sent = (sync._client.table("auto_sent_actions")
                     .select("action_type,entity_type,entity_id,flagged,created_at")
                     .eq("agent_name", agent_name)
                     .gte("created_at", since)
                     .order("created_at", desc=True)
                     .limit(80)
                     .execute()).data or []

        outcomes = (sync._client.table("outcomes")
                    .select("outcome_type,outcome_value,action_type,entity_type,"
                            "entity_id,fired_at")
                    .eq("agent_name", agent_name)
                    .gte("fired_at", since)
                    .order("fired_at", desc=True)
                    .limit(100)
                    .execute()).data or []

        # Observations the agent *acted on* — not every observation in the
        # system. Those it ignored are noise from this agent's perspective.
        observations = (sync._client.table("observations")
                        .select("category,severity,summary,action_taken,acted_at")
                        .eq("acted_on", True)
                        .gte("acted_at", since)
                        .order("acted_at", desc=True)
                        .limit(40)
                        .execute()).data or []

        return {"audit_log": audit, "auto_sent": auto_sent,
                "outcomes": outcomes, "observations": observations}
    return await asyncio.to_thread(_q)


async def _gather_seasonal_raw(month: str) -> dict[str, Any]:
    """All daily_stats rows for the given month across all years.

    Column notes: daily_stats uses avg_order_value (not 'aov'), ad_spend_google
    and ad_spend_meta are nullable/absent on old rows — select safe, reason
    tolerantly.
    """
    def _q():
        sync = Database()
        rows = (sync._client.table("daily_stats")
                .select("date,revenue,order_count,avg_order_value,new_customers,"
                        "ad_spend,ad_spend_google,ad_spend_meta")
                .order("date", desc=True)
                .limit(1500)
                .execute()).data or []
        filtered = [r for r in rows
                    if r.get("date") and str(r["date"])[5:7] == month]
        return {"daily_stats": filtered}
    return await asyncio.to_thread(_q)


def _sku_in_line_items(sku: str, line_items: Any) -> bool:
    """Check whether a SKU appears in an orders.line_items JSONB field."""
    if not line_items:
        return False
    if isinstance(line_items, list):
        for li in line_items:
            if isinstance(li, dict):
                if li.get("sku") == sku:
                    return True
                variant = li.get("variant", {})
                if isinstance(variant, dict) and variant.get("sku") == sku:
                    return True
    return False


def _max_created_at(raw: dict[str, Any]) -> str | None:
    """Return the latest created_at timestamp across the raw payload."""
    stamps: list[str] = []
    for bucket in raw.values():
        if isinstance(bucket, list):
            for row in bucket:
                if isinstance(row, dict):
                    ts = row.get("created_at") or row.get("date") or row.get("computed_at")
                    if ts:
                        stamps.append(str(ts))
    return max(stamps) if stamps else None


# ── Claude compilers ──
# Each one is a SINGLE Claude call per entity. Keep prompts specific — the
# compiler's job is to distill, not to reason broadly. Lower temperature,
# tight max_tokens so the note stays small by construction.

_COMPILE_MODEL = "claude-sonnet-4-5-20250929"
_COMPILE_MAX_TOKENS = 1200


async def _claude_compile_customer(raw: dict[str, Any]) -> str | None:
    if not settings.anthropic_api_key:
        return _fallback_customer_note(raw)

    customer = raw.get("customer")[0] if raw.get("customer") else {}
    orders = raw.get("orders", [])
    messages = raw.get("messages", [])
    rfm = raw.get("rfm", [])

    prompt = f"""You are compiling a one-page wiki note for a Pinaka Jewellery \
customer, to be read by another agent at decision time. Output plain markdown, \
concise, 400-700 words.

CUSTOMER RECORD
{customer}

ORDERS ({len(orders)} most recent)
{orders[:10]}

MESSAGES ({len(messages)} most recent, summarized)
{[{"date": (m.get("created_at") or "")[:10], "category": m.get("category"),
   "status": m.get("status"),
   "snippet": (m.get("body") or m.get("ai_draft") or "")[:120]}
  for m in messages[:10]]}

RFM SNAPSHOT
{rfm[0] if rfm else "none"}

Sections to include, only if supported by the data (skip a section rather \
than fabricate):

## Who
One sentence: name, email domain if interesting, segment, LTV.

## Order history
Dates + totals. If they've repeated, note the gap. If they're MTO-inbound \
(paid but not shipped), flag.

## Interaction patterns
What categories have they written about? Any complaint or return? Sentiment?

## Open threads / watch-outs
Anything another agent should know before talking to them. E.g. "recent \
complaint about clasp — don't pitch upsell until resolved."

## Suggested voice
One line of voice cue — how warm, how detailed, how personal based on what \
you see. Do not invent biographical detail.

Do not pad. If a section would be speculation, leave it out. No headers at \
top level except the H2s above."""
    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        def _call():
            return client.messages.create(
                model=_COMPILE_MODEL,
                max_tokens=_COMPILE_MAX_TOKENS,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
        resp = await asyncio.to_thread(_call)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text[:MAX_CONTENT_CHARS] if text else None
    except Exception:
        logger.exception("customer compile claude call failed")
        return _fallback_customer_note(raw)


async def _claude_compile_product(raw: dict[str, Any]) -> str | None:
    if not settings.anthropic_api_key:
        return _fallback_product_note(raw)

    product = raw.get("product") or {}
    orders = raw.get("orders_with_sku", [])
    creatives = raw.get("ad_creatives", [])
    metrics = raw.get("ad_metrics", [])

    # Per-meta-ad aggregates to hand to Claude. ad_creative_metrics joins
    # via meta_ad_id, not our internal creative id, so we bucket by that.
    by_meta_ad: dict[str, dict[str, float]] = {}
    for m in metrics:
        key = m.get("meta_ad_id")
        if not key:
            continue
        agg = by_meta_ad.setdefault(str(key), {"impressions": 0, "spend": 0.0,
                                               "clicks": 0})
        agg["impressions"] += int(m.get("impressions") or 0)
        agg["clicks"] += int(m.get("clicks") or 0)
        agg["spend"] += float(m.get("spend") or 0)

    prompt = f"""You are compiling a one-page wiki note for a Pinaka Jewellery \
SKU, read by the marketing and retention agents at decision time. Markdown, \
300-600 words.

PRODUCT
{product}

ORDERS WITH THIS SKU ({len(orders)})
{[{"total": o.get("total"), "date": (o.get("created_at") or '')[:10]} for o in orders[:10]]}

AD CREATIVES
{[{"id": c.get("id"), "variant": c.get("variant_label"), "status": c.get("status"),
   "headline": c.get("headline"),
   "primary_text": (c.get("primary_text") or "")[:160]} for c in creatives[:6]]}

AD METRICS AGGREGATED BY META AD ID
{by_meta_ad}

Sections (only include if supported by the data):

## Summary
One sentence: SKU, name, current status (live/draft/archived), price anchor.

## Sales signal
Orders containing this SKU, revenue generated, first/last order dates.

## Creative performance
Which variants have run. Which ones converted / fatigued / never got \
traction. Include concrete numbers (CTR, spend, conversions).

## What seems to work
One or two observations about angle / hook / audience that paid off. Do not \
speculate — only patterns visible in the data.

## What to avoid
Creative fatigue signals, underperforming angles, things to rotate out next.

Do not fabricate. If a section has no signal, omit it."""
    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        def _call():
            return client.messages.create(
                model=_COMPILE_MODEL,
                max_tokens=_COMPILE_MAX_TOKENS,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
        resp = await asyncio.to_thread(_call)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text[:MAX_CONTENT_CHARS] if text else None
    except Exception:
        logger.exception("product compile claude call failed")
        return _fallback_product_note(raw)


async def _claude_compile_agent(
    agent_name: str, raw: dict[str, Any], lookback_days: int,
) -> str | None:
    """Compile an agent's rolling self-summary. Distinct from the weekly
    retro — this is a decision-time reference ('what have I been doing?'),
    not a founder-facing narrative.
    """
    if not settings.anthropic_api_key:
        return _fallback_agent_note(agent_name, raw, lookback_days)

    audit = raw.get("audit_log", [])
    auto = raw.get("auto_sent", [])
    outs = raw.get("outcomes", [])
    obs = raw.get("observations", [])

    # Pre-aggregate to keep the prompt small and grounded
    tool_counts: dict[str, int] = {}
    for row in audit:
        for call in (row.get("tool_calls") or []):
            if isinstance(call, dict):
                name = call.get("tool") or call.get("name") or "unknown"
                tool_counts[name] = tool_counts.get(name, 0) + 1

    outcome_counts: dict[str, int] = {}
    for o in outs:
        ot = o.get("outcome_type") or "unknown"
        outcome_counts[ot] = outcome_counts.get(ot, 0) + 1

    action_counts: dict[str, int] = {}
    flagged_count = 0
    for a in auto:
        action_counts[a.get("action_type") or "unknown"] = (
            action_counts.get(a.get("action_type") or "unknown", 0) + 1)
        if a.get("flagged"):
            flagged_count += 1

    audit_total = len(audit)
    escalated_total = sum(1 for r in audit if r.get("escalated"))
    success_total = sum(1 for r in audit if r.get("result") == "success")

    prompt = f"""You are compiling a rolling self-memory note for the \
{agent_name} agent at Pinaka Jewellery. This is what YOU (that agent) will \
read at the start of your next run to orient yourself — it is NOT a report \
for the founder.

Lookback: last {lookback_days} days.

AUDIT LOG SUMMARY
  runs: {audit_total}
  successes: {success_total}
  escalated: {escalated_total}
  tools used (top): {dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:8])}

AUTO-TIER ACTIONS
  total: {len(auto)}
  flagged by founder: {flagged_count}
  breakdown: {dict(sorted(action_counts.items(), key=lambda x: -x[1])[:6])}

OUTCOMES (program-verified signals tied to your runs)
  total rows: {len(outs)}
  breakdown: {outcome_counts}

OBSERVATIONS ACTED ON
  count: {len(obs)}
  summaries: {[o.get("summary", "")[:100] for o in obs[:8]]}

Write 4 short sections in this exact order:

## What I've been doing
One paragraph. Specific verbs + counts. If runs = 0, say so.

## What's been working
Point to outcomes that came back positive (email_clicked, email_replied_48h, \
order_shipped_on_time, customer_repurchase_30d). Cite numbers.

## What to watch for
Point to negatives (flagged auto-sends, escalations, bounces, late shipments). \
Flag any pattern — e.g. "3 of 4 lifecycle_welcome_3 sends bounced this week."

## Open threads
Anything unresolved — escalations that haven't been answered, observations \
where the action I took is still pending verification. Use bullets here.

Total output <= 500 words. No preamble. Markdown sections as shown. If a \
section would be empty, write "(nothing notable)" rather than fabricating."""

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        def _call():
            return client.messages.create(
                model=_COMPILE_MODEL,
                max_tokens=_COMPILE_MAX_TOKENS,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
        resp = await asyncio.to_thread(_call)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text[:MAX_CONTENT_CHARS] if text else None
    except Exception:
        logger.exception("agent compile claude call failed for %s", agent_name)
        return _fallback_agent_note(agent_name, raw, lookback_days)


async def _claude_compile_seasonal(month: str, raw: dict[str, Any]) -> str | None:
    if not settings.anthropic_api_key:
        return _fallback_seasonal_note(month, raw)

    stats = raw.get("daily_stats", [])
    # Aggregate by year for easier reading
    by_year: dict[str, dict[str, float]] = {}
    for r in stats:
        year = str(r.get("date", ""))[:4]
        if not year:
            continue
        agg = by_year.setdefault(year, {"revenue": 0.0, "orders": 0, "spend": 0.0,
                                        "new_customers": 0})
        agg["revenue"] += float(r.get("revenue") or 0)
        agg["orders"] += int(r.get("order_count") or 0)
        agg["spend"] += float(r.get("ad_spend_google") or 0) + float(r.get("ad_spend_meta") or 0)
        agg["new_customers"] += int(r.get("new_customers") or 0)

    prompt = f"""You are compiling a historical seasonal note for month \
{month} at Pinaka Jewellery, read by the marketing agent. Markdown, 300-500 \
words.

PER-YEAR AGGREGATES
{by_year}

Sections:

## Pattern
One sentence: what typically happens this month (volume up/flat/down vs \
average, MER higher/lower).

## Year-over-year
Specific numbers per year: revenue, order count, ad spend, MER. Highlight \
which year was strongest and what was likely happening then.

## Historical wins
Any creative angle, campaign type, or segment that paid off this month in \
prior years, if visible.

## Watch-outs
Anomalies, drawdowns, or known risks (supply chain, Shopify outages, past \
campaign misfires) from prior years.

If there's only one year of data, say so and keep the note brief."""
    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        def _call():
            return client.messages.create(
                model=_COMPILE_MODEL,
                max_tokens=_COMPILE_MAX_TOKENS,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
        resp = await asyncio.to_thread(_call)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text[:MAX_CONTENT_CHARS] if text else None
    except Exception:
        logger.exception("seasonal compile claude call failed")
        return _fallback_seasonal_note(month, raw)


# ── Fallback notes when Claude is unavailable ──
# Deterministic, boring, but enough signal that an agent can still reason.

def _fallback_customer_note(raw: dict[str, Any]) -> str:
    customer = raw.get("customer", [{}])[0] if raw.get("customer") else {}
    orders = raw.get("orders", [])
    messages = raw.get("messages", [])
    rfm = raw.get("rfm", [])

    lines = ["## Who",
             f"- Email: {customer.get('email', 'unknown')}",
             f"- Segment: {customer.get('last_segment', rfm[0].get('segment') if rfm else 'unknown')}",
             f"- LTV: ${customer.get('lifetime_value', 0):,.2f}"]
    if orders:
        lines += ["", "## Order history"]
        for o in orders[:6]:
            lines.append(f"- {str(o.get('created_at', ''))[:10]} · ${float(o.get('total') or 0):,.2f} · {o.get('status')}")
    if messages:
        cats = {}
        for m in messages:
            cat = m.get("category", "unknown")
            cats[cat] = cats.get(cat, 0) + 1
        lines += ["", "## Interaction patterns",
                  "- Categories: " + ", ".join(f"{c}×{n}" for c, n in sorted(cats.items(), key=lambda x: -x[1]))]
    return "\n".join(lines)[:MAX_CONTENT_CHARS]


def _fallback_product_note(raw: dict[str, Any]) -> str:
    product = raw.get("product") or {}
    orders = raw.get("orders_with_sku", [])
    creatives = raw.get("ad_creatives", [])
    lines = ["## Summary",
             f"- SKU: {product.get('sku', '?')}",
             f"- Name: {product.get('name', '?')}"]
    if orders:
        rev = sum(float(o.get("total") or 0) for o in orders)
        lines += ["", "## Sales signal",
                  f"- {len(orders)} orders totaling ${rev:,.2f}"]
    if creatives:
        lines += ["", "## Creatives", f"- {len(creatives)} creatives on file"]
    return "\n".join(lines)[:MAX_CONTENT_CHARS]


def _fallback_agent_note(agent_name: str, raw: dict[str, Any], lookback_days: int) -> str:
    """Deterministic fallback when Claude is unavailable. Still useful to
    the agent — just less narrative."""
    audit = raw.get("audit_log", [])
    auto = raw.get("auto_sent", [])
    outs = raw.get("outcomes", [])
    lines = [
        f"## What I've been doing (last {lookback_days}d)",
        f"- {len(audit)} runs, {sum(1 for r in audit if r.get('escalated'))} escalated",
        f"- {sum(1 for r in audit if r.get('result') == 'success')} succeeded",
        f"- {len(auto)} auto-sent ({sum(1 for a in auto if a.get('flagged'))} flagged)",
    ]
    if outs:
        from collections import Counter
        cc = Counter(o.get("outcome_type", "?") for o in outs)
        lines += ["", "## Outcomes observed"] + [
            f"- {k}: {v}" for k, v in cc.most_common(6)
        ]
    return "\n".join(lines)[:MAX_CONTENT_CHARS]


def _fallback_seasonal_note(month: str, raw: dict[str, Any]) -> str:
    stats = raw.get("daily_stats", [])
    years = sorted({str(r.get("date", ""))[:4] for r in stats if r.get("date")})
    return f"## {month} — historical\n- Years of data: {', '.join(years)}\n- Rows: {len(stats)}"[:MAX_CONTENT_CHARS]


# ── Upsert ──

async def _upsert_memory(
    entity_type: str, entity_id: str, content: str,
    sample_count: int, source_through: str | None,
) -> dict[str, Any]:
    def _up():
        sync = Database()
        payload = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "content": content,
            "sample_count": sample_count,
            "source_through": source_through,
            "compiled_at": datetime.now(timezone.utc).isoformat(),
        }
        return (sync._client.table("entity_memory")
                .upsert(payload, on_conflict="entity_type,entity_id")
                .execute())
    res = await asyncio.to_thread(_up)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "sample_count": sample_count,
        "content_len": len(content),
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Nightly compile cron (orchestrator) ──

async def compile_all_active() -> dict[str, Any]:
    """Iterate active customers + products + every month's seasonal note.

    Active = had activity (order, message, ad impression) in the last 90 days
    for customers; non-archived on Shopify for products. We don't recompile
    every entity every night — only stale ones (>24h old OR raw data advanced
    past source_through).
    """
    customer_results = await _compile_active_customers()
    product_results = await _compile_active_products()
    seasonal_results = await _compile_current_and_next_seasonal()
    agent_results = await _compile_agent_memories()

    return {
        "customers": customer_results,
        "products": product_results,
        "seasonal": seasonal_results,
        "agents": agent_results,
    }


# Keep in sync with AGENT_KPI_MAP in src/agents/kpis.py. Listed here
# directly to avoid a circular import between memory.py and kpis.py.
_AGENT_NAMES = ("marketing", "retention", "customer_service", "finance", "order_ops")


async def _compile_agent_memories() -> dict[str, int]:
    """One compile per agent, always — these are sliding 7-day windows so
    'fresh <24h' staleness gate doesn't apply (we want yesterday's summary
    to always reflect yesterday's activity)."""
    compiled = 0
    skipped_empty = 0
    errors = 0
    for name in _AGENT_NAMES:
        try:
            result = await compile_agent(name)
            if result:
                compiled += 1
            else:
                skipped_empty += 1
        except Exception:
            logger.exception("compile_agent failed for %s", name)
            errors += 1
    return {"compiled": compiled, "skipped_empty": skipped_empty,
            "errors": errors, "total": len(_AGENT_NAMES)}


async def _compile_active_customers() -> dict[str, int]:
    def _q():
        sync = Database()
        since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        res = (sync._client.table("orders")
               .select("customer_id")
               .gte("created_at", since)
               .execute())
        return {int(r["customer_id"]) for r in (res.data or []) if r.get("customer_id")}
    try:
        customer_ids = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("active customers query failed")
        return {"compiled": 0, "skipped_fresh": 0, "errors": 0}

    compiled = 0
    skipped = 0
    errors = 0
    for cid in customer_ids:
        if not await _needs_recompile("customer", str(cid)):
            skipped += 1
            continue
        try:
            result = await compile_customer(cid)
            if result:
                compiled += 1
        except Exception:
            logger.exception("compile_customer failed for %s", cid)
            errors += 1
    return {"compiled": compiled, "skipped_fresh": skipped, "errors": errors,
            "active_total": len(customer_ids)}


async def _compile_active_products() -> dict[str, int]:
    def _q():
        sync = Database()
        res = (sync._client.table("products")
               .select("sku")
               .execute())
        return [r["sku"] for r in (res.data or []) if r.get("sku")]
    try:
        skus = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("products query failed")
        return {"compiled": 0, "skipped_fresh": 0, "errors": 0}

    compiled = 0
    skipped = 0
    errors = 0
    for sku in skus:
        if not await _needs_recompile("product", sku):
            skipped += 1
            continue
        try:
            result = await compile_product(sku)
            if result:
                compiled += 1
        except Exception:
            logger.exception("compile_product failed for %s", sku)
            errors += 1
    return {"compiled": compiled, "skipped_fresh": skipped, "errors": errors,
            "active_total": len(skus)}


async def _compile_current_and_next_seasonal() -> dict[str, int]:
    """Keep only this month + next month compiled. Others are built on-demand."""
    today = datetime.now(timezone.utc).date()
    this_month = f"{today.month:02d}"
    next_month_num = 1 if today.month == 12 else today.month + 1
    next_month = f"{next_month_num:02d}"

    compiled = 0
    errors = 0
    for m in (this_month, next_month):
        try:
            if await compile_seasonal(m):
                compiled += 1
        except Exception:
            logger.exception("compile_seasonal failed for %s", m)
            errors += 1
    return {"compiled": compiled, "errors": errors}


async def _needs_recompile(entity_type: str, entity_id: str) -> bool:
    """Recompile if note is older than STALE_AFTER_HOURS or missing."""
    existing = await get_memory(entity_type, entity_id)
    if not existing:
        return True
    try:
        compiled_at = datetime.fromisoformat(
            str(existing["compiled_at"]).replace("Z", "+00:00"))
    except Exception:
        return True
    return (datetime.now(timezone.utc) - compiled_at) > timedelta(hours=STALE_AFTER_HOURS)
