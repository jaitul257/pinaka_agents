"""Weekly agent retrospectives — Phase 12.4.

Every Monday 8 AM ET, each agent writes a 2-paragraph self-review of the
previous week based on:
  - its audit log (what tools it invoked, success vs escalated counts)
  - its AUTO-tier actions (how many mechanical items it handled)
  - its KPI trend (did its number move?)

Replaces "review 100 individual approvals" with "read 5 retros." Founder
scans the retros in Slack + /dashboard/agents; individual audit rows are
one click deeper if anything looks off.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from anthropic import Anthropic

from src.agents.kpis import AGENT_KPI_MAP, latest_kpi
from src.core.database import Database
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


AGENTS = list(AGENT_KPI_MAP.keys())


def _week_start(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())  # Monday


async def generate_weekly_retros() -> list[dict[str, Any]]:
    week_start = _week_start() - timedelta(days=7)  # Monday of LAST week
    week_end = week_start + timedelta(days=7)

    retros: list[dict[str, Any]] = []
    for agent_name in AGENTS:
        summary = await _one_retro(agent_name, week_start, week_end)
        if summary:
            retros.append(summary)

    if retros:
        try:
            await _post_combined_slack(retros, week_start)
        except Exception:
            logger.exception("weekly retros slack post failed")

    return retros


async def _one_retro(agent_name: str, week_start: date, week_end: date) -> dict[str, Any] | None:
    actions = await _actions_summary(agent_name, week_start, week_end)
    auto_actions = await _auto_actions_summary(agent_name, week_start, week_end)
    kpi = await latest_kpi(agent_name)

    if actions["total_runs"] == 0 and auto_actions["total"] == 0:
        return None

    narrative, needs = await _claude_narrative(
        agent_name, actions, auto_actions, kpi, week_start, week_end,
    )

    snapshot = {}
    if kpi:
        snapshot = {
            "kpi_name": kpi.get("kpi_name"),
            "value": kpi.get("value"),
            "trend_7d": kpi.get("trend_7d"),
        }

    actions_summary = {
        "runs": actions["total_runs"],
        "escalated": actions["escalated"],
        "auto_sent": auto_actions["total"],
        "auto_flagged": auto_actions["flagged"],
        "by_tool": actions["by_tool"],
    }

    await _upsert_retro(agent_name, week_start, narrative, snapshot, actions_summary, needs)

    return {
        "agent_name": agent_name,
        "week_start": week_start.isoformat(),
        "summary": narrative,
        "needs": needs,
        "kpi": snapshot,
        "actions": actions_summary,
    }


async def _actions_summary(agent_name: str, start: date, end: date) -> dict[str, Any]:
    def _q():
        sync = Database()
        return (sync._client.table("agent_audit_log")
                .select("tool_calls,result,escalated")
                .eq("agent_name", agent_name)
                .gte("created_at", start.isoformat())
                .lt("created_at", end.isoformat())
                .execute())
    try:
        res = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("actions_summary query failed for %s", agent_name)
        return {"total_runs": 0, "escalated": 0, "by_tool": {}, "success_rate": 0.0}

    rows = res.data or []
    total = len(rows)
    escalated = sum(1 for r in rows if r.get("escalated"))
    success = sum(1 for r in rows if r.get("result") == "success")
    by_tool: dict[str, int] = {}
    for r in rows:
        calls = r.get("tool_calls") or []
        for call in calls:
            if isinstance(call, dict):
                name = call.get("tool") or call.get("name") or "unknown"
                by_tool[name] = by_tool.get(name, 0) + 1

    return {
        "total_runs": total,
        "escalated": escalated,
        "by_tool": dict(sorted(by_tool.items(), key=lambda x: -x[1])[:8]),
        "success_rate": (success / total * 100) if total else 0.0,
    }


async def _auto_actions_summary(agent_name: str, start: date, end: date) -> dict[str, Any]:
    def _q():
        sync = Database()
        return (sync._client.table("auto_sent_actions")
                .select("action_type,flagged")
                .eq("agent_name", agent_name)
                .gte("created_at", start.isoformat())
                .lt("created_at", end.isoformat())
                .execute())
    try:
        res = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("auto_actions_summary query failed for %s", agent_name)
        return {"total": 0, "flagged": 0, "by_type": {}}

    rows = res.data or []
    total = len(rows)
    flagged = sum(1 for r in rows if r.get("flagged"))
    by_type: dict[str, int] = {}
    for r in rows:
        t = r.get("action_type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total": total,
        "flagged": flagged,
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])[:6]),
    }


async def _claude_narrative(
    agent_name: str,
    actions: dict[str, Any],
    auto_actions: dict[str, Any],
    kpi: dict[str, Any] | None,
    week_start: date,
    week_end: date,
) -> tuple[str, str | None]:
    if not settings.anthropic_api_key:
        return _fallback_narrative(agent_name, actions, auto_actions, kpi), None

    kpi_line = "No KPI computed this week."
    if kpi:
        trend = kpi.get("trend_7d")
        trend_str = f" ({trend:+.1f}% vs last week)" if trend is not None else ""
        kpi_line = f"{kpi['kpi_name']} = {kpi['value']}{trend_str}"

    prompt = f"""You are the {agent_name} agent for Pinaka Jewellery, writing your own weekly retrospective. Plain text, second person (\"I\"), founder Jaitul is the audience.

Week of {week_start.isoformat()} → {week_end.isoformat()}.

KPI: {kpi_line}

Actions taken (from audit log): {actions['total_runs']} agent runs, {actions['escalated']} escalated to founder, {actions['success_rate']:.0f}% success.
Top tools used: {actions['by_tool']}

Auto-sent actions (no approval needed): {auto_actions['total']} sent, {auto_actions['flagged']} flagged by founder.
Breakdown: {auto_actions['by_type']}

Write TWO short paragraphs:
1. "What I did" — specific accomplishments, with numbers. No fluff.
2. "What's next" — one concrete initiative for next week.

Then on the last line, one sentence after "NEEDS:" describing what you need from the founder (only if actually blocked — otherwise write NEEDS: none).

Total output ~120 words. No headers, no bullets."""

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        def _call():
            return client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
        resp = await asyncio.to_thread(_call)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

        needs: str | None = None
        narrative = text
        if "NEEDS:" in text:
            narrative, needs_raw = text.rsplit("NEEDS:", 1)
            needs_raw = needs_raw.strip()
            if needs_raw.lower() not in ("none", "none.", ""):
                needs = needs_raw
        return narrative.strip(), needs
    except Exception:
        logger.exception("claude narrative failed for %s", agent_name)
        return _fallback_narrative(agent_name, actions, auto_actions, kpi), None


def _fallback_narrative(
    agent_name: str, actions: dict[str, Any],
    auto_actions: dict[str, Any], kpi: dict[str, Any] | None,
) -> str:
    kpi_line = ""
    if kpi:
        trend = kpi.get("trend_7d")
        tr = f" ({trend:+.1f}% WoW)" if trend is not None else ""
        kpi_line = f" KPI: {kpi['kpi_name']} = {kpi['value']}{tr}."
    return (
        f"This week I ran {actions['total_runs']} agent cycles "
        f"({actions['success_rate']:.0f}% success, {actions['escalated']} escalated) "
        f"and auto-sent {auto_actions['total']} routine actions "
        f"({auto_actions['flagged']} flagged).{kpi_line}"
    )


async def _upsert_retro(
    agent_name: str, week_start: date, summary: str,
    kpi_snapshot: dict[str, Any], actions_summary: dict[str, Any],
    needs: str | None,
) -> None:
    def _up():
        sync = Database()
        return sync._client.table("agent_retros").upsert({
            "agent_name": agent_name,
            "week_start": week_start.isoformat(),
            "summary_text": summary,
            "kpi_snapshot": kpi_snapshot,
            "actions_summary": actions_summary,
            "needs_from_founder": needs,
        }, on_conflict="agent_name,week_start").execute()
    try:
        await asyncio.to_thread(_up)
    except Exception:
        logger.exception("upsert_retro failed for %s", agent_name)


async def latest_retros(limit_per_agent: int = 1) -> list[dict[str, Any]]:
    """For dashboard rendering."""
    def _q(agent: str):
        sync = Database()
        return (sync._client.table("agent_retros")
                .select("*")
                .eq("agent_name", agent)
                .order("week_start", desc=True)
                .limit(limit_per_agent)
                .execute())
    out: list[dict[str, Any]] = []
    for agent in AGENTS:
        try:
            res = await asyncio.to_thread(_q, agent)
            out.extend(res.data or [])
        except Exception:
            logger.exception("latest_retros read failed for %s", agent)
    return out


async def _post_combined_slack(retros: list[dict[str, Any]], week_start: date) -> None:
    slack = SlackNotifier()
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f":bar_chart: Agent retros — week of {week_start.isoformat()}"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn",
             "text": "Each agent's self-review. Dashboard: <https://pinaka-agents-production-198b5.up.railway.app/dashboard/agents|/dashboard/agents>"}
        ]},
        {"type": "divider"},
    ]
    for r in retros:
        kpi = r.get("kpi", {})
        kpi_str = ""
        if kpi and kpi.get("value") is not None:
            trend = kpi.get("trend_7d")
            tr = f" ({trend:+.1f}%)" if trend is not None else ""
            kpi_str = f" · *{kpi['kpi_name']}*: {kpi['value']}{tr}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{r['agent_name']}*{kpi_str}\n{r['summary']}"}})
        if r.get("needs"):
            blocks.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": f":information_source: _Needs from you: {r['needs']}_"}
            ]})
        blocks.append({"type": "divider"})
    await slack.send_blocks(blocks, text=f"Agent retros week {week_start.isoformat()}")
