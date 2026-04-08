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
from datetime import datetime, timedelta
from typing import Any

import anthropic

from src.core.database import Database
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)

HEARTBEAT_PROMPT = """You are the Heartbeat Monitor for Pinaka Jewellery's AI operations system.

You've just received a list of issues found during a routine health check. For each issue,
decide what action to take:

ACTIONS AVAILABLE:
- ALERT: Post to Slack for founder attention (use for critical/ambiguous issues)
- DISPATCH: Recommend which agent should handle this (order_ops, customer_service, marketing, finance, retention)
- MONITOR: No action needed yet, but flag for next heartbeat check
- DISMISS: Not a real issue (e.g., test data, expected behavior)

For each issue, respond with a JSON array:
[
  {"issue": "summary", "action": "ALERT|DISPATCH|MONITOR|DISMISS", "agent": "agent_name or null", "reason": "why", "priority": "high|medium|low"}
]

Be conservative. Only ALERT or DISPATCH when there's a real business impact. Most observations
are routine and should be MONITOR or DISMISS.
"""


class Heartbeat:
    """Periodic awareness scan — finds things that fell through the cracks."""

    def __init__(self):
        self._db = Database()
        self._slack = SlackNotifier()
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def beat(self) -> dict[str, Any]:
        """Run one heartbeat cycle. Returns a summary of actions taken."""
        start = datetime.utcnow()
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
        alerts_sent = 0
        dispatches = 0
        for action in actions:
            if action.get("action") == "ALERT":
                await self._send_alert(action)
                alerts_sent += 1
            elif action.get("action") == "DISPATCH":
                await self._dispatch_agent(action)
                dispatches += 1

        # Mark observations as acted on
        await self._mark_observations_acted(issues)

        await self._increment_counter("total_beats")
        if alerts_sent > 0 or dispatches > 0:
            await self._increment_counter("beats_with_action")

        summary = {
            "status": "acted",
            "issues": len(issues),
            "alerts_sent": alerts_sent,
            "dispatches": dispatches,
            "duration_ms": int((datetime.utcnow() - start).total_seconds() * 1000),
        }
        logger.info("Heartbeat: %d issues, %d alerts, %d dispatches", len(issues), alerts_sent, dispatches)
        return summary

    # ── Cheap SQL checks ──

    async def _check_stuck_orders(self) -> list[dict]:
        """Orders in 'paid' status for > 48 hours with no ShipStation ID."""
        cutoff = (datetime.utcnow() - timedelta(hours=48)).isoformat()
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
        cutoff = (datetime.utcnow() - timedelta(hours=2)).isoformat()
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
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
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
        cutoff = (datetime.utcnow() - timedelta(hours=6)).isoformat()
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

    async def _reason_about_issues(self, issues: list[dict]) -> list[dict]:
        """Ask Claude to triage issues and decide actions."""
        issues_text = json.dumps(
            [{"severity": i["severity"], "summary": i["summary"], "check": i["check"]} for i in issues],
            indent=2,
        )

        try:
            response = await self._client.messages.create(
                model=settings.claude_model,
                system=HEARTBEAT_PROMPT,
                max_tokens=1024,
                messages=[{"role": "user", "content": f"Issues found during heartbeat scan:\n\n{issues_text}"}],
            )

            text = response.content[0].text.strip()

            # Extract JSON from response
            import re
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return []
        except Exception:
            logger.exception("Heartbeat Claude reasoning failed")
            # Fall back: alert on all critical issues
            return [
                {"issue": i["summary"], "action": "ALERT", "agent": None, "reason": "Claude reasoning failed, alerting by default", "priority": "high"}
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
                    .update({"acted_on": True, "acted_at": datetime.utcnow().isoformat(), "action_taken": "heartbeat_processed"})
                    .in_("id", obs_ids)
                    .execute()
                )
            except Exception:
                logger.exception("Failed to mark observations as acted")

    async def _update_state(self, key: str, value: dict) -> None:
        try:
            await asyncio.to_thread(
                lambda: self._db._client.table("heartbeat_state")
                .upsert({"key": key, "value": value, "updated_at": datetime.utcnow().isoformat()})
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
