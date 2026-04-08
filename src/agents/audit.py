"""AuditLogger — records every agent decision for debugging and trust-building.

Every agent run logs: what tools were called, what policies fired, whether
the agent escalated, how many tokens were used, and how long it took.

Stored in Supabase `agent_audit_log` table (migration 20260407120000).
"""

import asyncio
import logging
from typing import Any

from src.core.database import Database

logger = logging.getLogger(__name__)


class AuditLogger:
    """Log agent runs to the agent_audit_log table."""

    def __init__(self):
        self._db = Database()

    async def log(
        self,
        agent_name: str,
        task: str,
        tool_calls: list[dict[str, Any]],
        policy_decisions: list[dict[str, Any]],
        result: str,
        tokens_used: int,
        duration_ms: int,
        escalated: bool = False,
    ) -> str | None:
        """Write an audit log entry. Returns the row ID or None on failure."""
        try:
            data = {
                "agent_name": agent_name,
                "task_summary": task[:500],
                "tool_calls": tool_calls,
                "policy_decisions": policy_decisions,
                "result": result,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
                "escalated": escalated,
            }
            row = await asyncio.to_thread(
                lambda: self._db._client.table("agent_audit_log").insert(data).execute()
            )
            if row.data:
                return str(row.data[0].get("id", ""))
            return None
        except Exception:
            logger.exception("Failed to write agent audit log")
            return None

    async def get_recent(
        self, agent_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch recent audit entries, optionally filtered by agent name."""
        try:
            def _query():
                query = self._db._client.table("agent_audit_log").select("*")
                if agent_name:
                    query = query.eq("agent_name", agent_name)
                return query.order("created_at", desc=True).limit(limit).execute()

            result = await asyncio.to_thread(_query)
            return result.data or []
        except Exception:
            logger.exception("Failed to read agent audit log")
            return []

    async def get_tokens_used_today(self) -> int:
        """Sum tokens used across all agents today. Used by TokenBudgetPolicy."""
        from datetime import date

        try:
            today = date.today().isoformat()

            def _query():
                return (
                    self._db._client.table("agent_audit_log")
                    .select("tokens_used")
                    .gte("created_at", today)
                    .execute()
                )

            result = await asyncio.to_thread(_query)
            return sum(int(r.get("tokens_used", 0)) for r in (result.data or []))
        except Exception:
            logger.exception("Failed to sum today's token usage")
            return 0
