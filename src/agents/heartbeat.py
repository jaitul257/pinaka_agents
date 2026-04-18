"""Heartbeat — persistent awareness for the agent system.

Runs every 30 minutes via cron. Does cheap SQL checks first, only invokes
Claude when something genuinely needs attention.

Awareness checks (no LLM cost):
1. Stuck orders — paid > 48h with no ShipStation tracking
2. Unanswered messages — pending_review > 2h
3. Shipping delays — shipped > 7 days, no delivery confirmation
4. ROAS alerts — dropped below maintain threshold
5. Abandoned carts — high-value carts not recovered
6. Agent failures — recent audit entries with escalated=true or result=failed

When issues are found, Claude reasons about them and decides:
- Which agent to dispatch (or just post to Slack)
- Priority ordering
- Whether to combine related issues
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic

from src.core.database import Database
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)

HEARTBEAT_PROMPT = """You are the Heartbeat Monitor for Pinaka Jewellery's AI operations system.

## Your job

Review a snapshot of system state against the thresholds we use to flag \
anomalies. Report findings. Recommend action ONLY if the data supports it.

You are not a problem-finder. You are a verifier. Most heartbeats should \
conclude "state is within thresholds, no action needed." That is a correct \
and valuable outcome.

## What each item in the input means

Each item carries a `check` name, a `severity` flag, a `summary`, and a \
`data` payload containing the raw values and the thresholds that triggered \
the flag. Reason against the actual numbers — do not assume the flag is \
correct just because it's in the list.

A stuck-order flag at 49 hours on a Sunday is different from one at 72 hours \
on a Tuesday. A shipping_delay on a 10-day-old overseas order is different \
from one on a 10-day-old domestic order. The data payload tells you which.

## Actions available

For each item, choose exactly one of:

- NO_ACTION: state is within acceptable variance given the data. Preferred \
  when thresholds are technically crossed but the business context explains \
  it (weekend, holiday, seasonal window, known-long lead time).
- MONITOR: flag to re-check in the next cycle without alerting now. Use when \
  something is drifting but has not yet crossed a material line.
- DISPATCH: recommend a specific agent handle this (order_ops, \
  customer_service, marketing, finance, retention). Only when there is a \
  concrete action an agent can take to resolve it.
- ALERT: post to Slack for founder attention. Reserved for cases where \
  (a) there is real business impact AND (b) no agent can resolve it \
  autonomously. Do not ALERT just because something is unusual.

## Output format

Respond with a JSON array, one entry per input item, in the same order:

[
  {"issue": "summary", "action": "NO_ACTION|MONITOR|DISPATCH|ALERT", \
"agent": "agent_name or null", "reason": "specific numeric comparison, e.g. \
'47h < 48h threshold, within variance'", "priority": "high|medium|low"}
]

The `reason` field MUST cite specific numbers or conditions from the data \
payload. A reason like "looks fine" is not acceptable; "47h elapsed vs 48h \
threshold, and weekend explains the gap" is.

## Default assumption

If you cannot confidently justify ALERT or DISPATCH from the data, choose \
MONITOR or NO_ACTION. Silence is valid. Over-alerting costs founder \
attention and trains the system to escalate normal variance.
"""


class Heartbeat:
    """Periodic awareness scan — finds things that fell through the cracks."""

    def __init__(self):
        self._db = Database()
        self._slack = SlackNotifier()
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def beat(self) -> dict[str, Any]:
        """Run one heartbeat cycle. Returns a summary of actions taken."""
        start = datetime.now(timezone.utc)
        issues: list[dict[str, Any]] = []

        # ── Cheap SQL checks (no LLM) ──
        issues.extend(await self._check_stuck_orders())
        issues.extend(await self._check_unanswered_messages())
        issues.extend(await self._check_shipping_delays())
        issues.extend(await self._check_unacted_observations())
        issues.extend(await self._check_agent_failures())

        # Update heartbeat state
        await self._update_state("last_run", {
            "timestamp": start.isoformat(),
            "issues_found": len(issues),
        })

        if not issues:
            logger.info("Heartbeat: all clear, no issues found")
            await self._increment_counter("total_beats")
            return {"status": "clear", "issues": 0, "actions": []}

        # ── Claude reasoning (only when issues exist) ──
        actions = await self._reason_about_issues(issues)

        # ── Execute actions ──
        # NO_ACTION and MONITOR are deliberate no-ops: we count them so the
        # dashboard knows the heartbeat actually reviewed items (not silence),
        # but we do not page the founder for them.
        alerts_sent = 0
        dispatches = 0
        monitored = 0
        no_action = 0
        for action in actions:
            verb = (action.get("action") or "").upper()
            if verb == "ALERT":
                await self._send_alert(action)
                alerts_sent += 1
            elif verb == "DISPATCH":
                await self._dispatch_agent(action)
                dispatches += 1
            elif verb == "MONITOR":
                monitored += 1
            elif verb in ("NO_ACTION", "DISMISS"):
                no_action += 1

        # Mark observations as acted on
        await self._mark_observations_acted(issues)

        await self._increment_counter("total_beats")
        if alerts_sent > 0 or dispatches > 0:
            await self._increment_counter("beats_with_action")

        summary = {
            "status": "acted" if (alerts_sent or dispatches) else "reviewed",
            "issues": len(issues),
            "alerts_sent": alerts_sent,
            "dispatches": dispatches,
            "monitored": monitored,
            "no_action": no_action,
            "duration_ms": int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        }
        logger.info(
            "Heartbeat: %d items, %d alerts, %d dispatches, %d monitor, %d no-action",
            len(issues), alerts_sent, dispatches, monitored, no_action,
        )
        return summary

    # ── Cheap SQL checks ──

    async def _check_stuck_orders(self) -> list[dict]:
        """Orders in 'paid' status for > 48 hours with no ShipStation ID."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        result = await asyncio.to_thread(
            lambda: self._db._client.table("orders")
            .select("shopify_order_id, buyer_name, total, created_at, status")
            .eq("status", "paid")
            .is_("shipstation_order_id", "null")
            .lte("created_at", cutoff)
            .execute()
        )
        return [
            {
                "check": "stuck_order",
                "severity": "warning",
                "summary": f"Order #{r['shopify_order_id']} ({r['buyer_name']}, ${float(r['total']):,.2f}) stuck in 'paid' for >48h — no ShipStation order created",
                "entity_type": "order",
                "entity_id": str(r["shopify_order_id"]),
                "data": r,
            }
            for r in (result.data or [])
        ]

    async def _check_unanswered_messages(self) -> list[dict]:
        """Customer messages in pending_review for > 2 hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        result = await asyncio.to_thread(
            lambda: self._db._client.table("messages")
            .select("id, customer_email, category, created_at")
            .eq("status", "pending_review")
            .lte("created_at", cutoff)
            .execute()
        )
        return [
            {
                "check": "unanswered_message",
                "severity": "warning",
                "summary": f"Customer message from {r['customer_email']} ({r['category']}) unanswered for >2h",
                "entity_type": "customer",
                "entity_id": r["customer_email"],
                "data": r,
            }
            for r in (result.data or [])
        ]

    async def _check_shipping_delays(self) -> list[dict]:
        """Orders shipped > 7 days ago with no delivery confirmation."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        result = await asyncio.to_thread(
            lambda: self._db._client.table("orders")
            .select("shopify_order_id, buyer_name, total, shipped_at, tracking_number")
            .eq("status", "shipped")
            .is_("delivered_at", "null")
            .lte("shipped_at", cutoff)
            .execute()
        )
        return [
            {
                "check": "shipping_delay",
                "severity": "warning",
                "summary": f"Order #{r['shopify_order_id']} shipped >7 days ago, no delivery confirmation (tracking: {r.get('tracking_number', 'none')})",
                "entity_type": "order",
                "entity_id": str(r["shopify_order_id"]),
                "data": r,
            }
            for r in (result.data or [])
        ]

    async def _check_unacted_observations(self) -> list[dict]:
        """Critical/warning observations not yet acted on."""
        result = await asyncio.to_thread(
            lambda: self._db._client.table("observations")
            .select("id, source, category, severity, summary, entity_type, entity_id, data, created_at")
            .eq("acted_on", False)
            .in_("severity", ["critical", "warning"])
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        return [
            {
                "check": "unacted_observation",
                "severity": r["severity"],
                "summary": r["summary"],
                "entity_type": r.get("entity_type"),
                "entity_id": r.get("entity_id"),
                "data": {"observation_id": r["id"], "source": r["source"], **r.get("data", {})},
            }
            for r in (result.data or [])
        ]

    async def _check_agent_failures(self) -> list[dict]:
        """Agent runs that failed or escalated in the last 6 hours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        result = await asyncio.to_thread(
            lambda: self._db._client.table("agent_audit_log")
            .select("agent_name, task_summary, result, escalated, created_at")
            .gte("created_at", cutoff)
            .or_("result.eq.failed,escalated.eq.true")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return [
            {
                "check": "agent_failure",
                "severity": "warning",
                "summary": f"Agent '{r['agent_name']}' {r['result']} (escalated={r['escalated']}): {r['task_summary'][:100]}",
                "entity_type": "agent",
                "entity_id": r["agent_name"],
                "data": r,
            }
            for r in (result.data or [])
        ]

    # ── Claude reasoning ──

    # Threshold definitions — shared with the agent's reasoning context
    # so Claude can compare observed values to the line that was crossed,
    # not just trust the flag.
    _THRESHOLDS: dict[str, str] = {
        "stuck_order": "paid > 48h with no ShipStation tracking",
        "unanswered_message": "pending_review > 2h",
        "shipping_delay": "shipped > 7d with no delivered_at",
        "unacted_observation": "observation.severity in (warning, critical) AND acted_on=false",
        "agent_failure": "audit_log in last 6h with result=failed OR escalated=true",
    }

    async def _reason_about_issues(self, issues: list[dict]) -> list[dict]:
        """Ask Claude to triage findings and decide actions.

        We include the full data payload AND the threshold definition for
        each check so Claude is comparing observed vs expected, not just
        parroting the flag. Prompt framing is neutral (verify, don't hunt).
        """
        enriched = [
            {
                "check": i["check"],
                "severity": i["severity"],
                "threshold": self._THRESHOLDS.get(i["check"], "unknown threshold"),
                "summary": i["summary"],
                "data": i.get("data", {}),
            }
            for i in issues
        ]
        issues_text = json.dumps(enriched, indent=2, default=str)

        try:
            response = await self._client.messages.create(
                model=settings.claude_model,
                system=HEARTBEAT_PROMPT,
                max_tokens=1024,
                messages=[{"role": "user",
                           "content": f"Snapshot of flagged items with raw data and thresholds:\n\n{issues_text}"}],
            )

            text = response.content[0].text.strip()
            import re
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if not match:
                return []
            parsed = json.loads(match.group())
            # Keep only items that imply action — NO_ACTION and MONITOR are
            # deliberately dropped here so _send_alert / _dispatch_agent
            # never fires on them. Statistics live in the return summary.
            return parsed
        except Exception:
            logger.exception("Heartbeat Claude reasoning failed")
            # Fall back: alert on items explicitly marked 'critical' in the
            # input severity — not 'warning'. Warning-level items wait for
            # the next heartbeat when Claude is working again.
            return [
                {"issue": i["summary"], "action": "ALERT", "agent": None,
                 "reason": "Claude reasoning failed; critical-severity item escalated by fallback policy",
                 "priority": "high"}
                for i in issues if i["severity"] == "critical"
            ]

    # ── Action execution ──

    async def _send_alert(self, action: dict) -> None:
        """Post an alert to Slack."""
        priority_emoji = {"high": ":rotating_light:", "medium": ":warning:", "low": ":information_source:"}.get(action.get("priority", "medium"), ":warning:")
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{priority_emoji} Heartbeat Alert"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Issue:* {action.get('issue', 'Unknown')}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason:* {action.get('reason', '')}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Priority: {action.get('priority', 'medium')} | Heartbeat automated scan_"}]},
        ]
        try:
            await self._slack.send_blocks(blocks, text=f"Heartbeat: {action.get('issue', '')[:100]}")
        except Exception:
            logger.exception("Failed to send heartbeat alert to Slack")

    async def _dispatch_agent(self, action: dict) -> None:
        """Dispatch an agent to handle an issue. For now, just alerts — full dispatch in next iteration."""
        agent_name = action.get("agent", "unknown")
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f":robot_face: Heartbeat → {agent_name}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Issue:* {action.get('issue', 'Unknown')}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Recommended agent:* `{agent_name}`\n*Reason:* {action.get('reason', '')}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Auto-dispatch coming soon. For now, this is an alert._"}]},
        ]
        try:
            await self._slack.send_blocks(blocks, text=f"Heartbeat dispatch: {agent_name}")
        except Exception:
            logger.exception("Failed to send heartbeat dispatch to Slack")

    # ── State management ──

    async def _mark_observations_acted(self, issues: list[dict]) -> None:
        """Mark observation rows as acted on."""
        obs_ids = [
            i["data"].get("observation_id")
            for i in issues
            if i["check"] == "unacted_observation" and i.get("data", {}).get("observation_id")
        ]
        if obs_ids:
            try:
                await asyncio.to_thread(
                    lambda: self._db._client.table("observations")
                    .update({"acted_on": True, "acted_at": datetime.now(timezone.utc).isoformat(), "action_taken": "heartbeat_processed"})
                    .in_("id", obs_ids)
                    .execute()
                )
            except Exception:
                logger.exception("Failed to mark observations as acted")

    async def _update_state(self, key: str, value: dict) -> None:
        try:
            await asyncio.to_thread(
                lambda: self._db._client.table("heartbeat_state")
                .upsert({"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()})
                .execute()
            )
        except Exception:
            logger.exception("Failed to update heartbeat state")

    async def _increment_counter(self, counter_name: str) -> None:
        try:
            result = await asyncio.to_thread(
                lambda: self._db._client.table("heartbeat_state")
                .select("value")
                .eq("key", "counters")
                .execute()
            )
            counters = (result.data[0]["value"] if result.data else {})
            counters[counter_name] = counters.get(counter_name, 0) + 1
            await self._update_state("counters", counters)
        except Exception:
            logger.exception("Failed to increment heartbeat counter")
