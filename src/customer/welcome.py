"""Welcome educational series (Phase 9.2).

5 static emails over 18 days for any new customer who hasn't purchased yet.
No Claude, no Slack approval — pre-vetted educational copy in SendGrid
templates. Purely additive: existing order-confirmation flow unchanged.

Cohort entry: `customers/create` webhook calls `start_welcome_series()`.
Daily cron picks up customers whose days-since-start matches a step boundary
(0, 3, 7, 12, 18) and the step hasn't been sent yet. A purchase zeros them
out of the cohort (lifecycle orchestrator takes over).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.database import AsyncDatabase
from src.core.email import EmailSender
from src.core.settings import settings

logger = logging.getLogger(__name__)


WELCOME_STEPS = [1, 2, 3, 4, 5]


class WelcomeSeriesEngine:
    """Evaluate the welcome cohort and send each customer's next due step."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._email = EmailSender()

    async def send_due(self) -> dict[str, Any]:
        candidates = await self._db.get_welcome_candidates()
        if not candidates:
            return {"sent": 0, "candidates": 0, "skipped_missing_template": 0, "failed": 0}

        sent = 0
        failed = 0
        skipped = 0
        for cand in candidates[:50]:  # safety cap
            step = int(cand.get("_next_step") or 0)
            email_addr = cand.get("email") or ""
            name = cand.get("name") or email_addr
            customer_id = cand.get("id")
            if not email_addr or not customer_id or step not in WELCOME_STEPS:
                continue

            ok = await _send_welcome_step(self._email, email_addr, name, step,
                                           customer_id=customer_id)
            if ok is None:
                skipped += 1
                continue
            if ok:
                try:
                    await self._db.mark_welcome_step_sent(int(customer_id), step)
                    sent += 1
                    try:
                        from src.agents.approval_tiers import log_auto_sent
                        await log_auto_sent(
                            agent_name="retention",
                            action_type=f"lifecycle_welcome_{step}",
                            entity_type="customer",
                            entity_id=str(customer_id),
                            payload={"email": email_addr, "step": step},
                        )
                    except Exception:
                        logger.exception("auto_sent log failed for welcome step %d", step)
                except Exception:
                    logger.exception("Failed to mark welcome step for %s/%d",
                                     customer_id, step)
                    failed += 1
            else:
                failed += 1

        logger.info(
            "Welcome: sent=%d failed=%d skipped=%d across %d candidates",
            sent, failed, skipped, len(candidates),
        )
        return {
            "candidates": len(candidates),
            "sent": sent,
            "failed": failed,
            "skipped_missing_template": skipped,
        }


async def _send_welcome_step(
    email: EmailSender,
    to_email: str,
    name: str,
    step: int,
    customer_id: int | str | None = None,
) -> bool | None:
    """Thin async wrapper around the sync SendGrid call.

    Returns True on success, False on send failure, None if no template is
    configured for this step (not an error — just un-configured).
    """
    import asyncio
    # Check template configured
    template_id = {
        1: settings.sendgrid_welcome_1_template_id,
        2: settings.sendgrid_welcome_2_template_id,
        3: settings.sendgrid_welcome_3_template_id,
        4: settings.sendgrid_welcome_4_template_id,
        5: settings.sendgrid_welcome_5_template_id,
    }.get(step, "")
    if not template_id:
        return None

    return await asyncio.to_thread(
        email.send_welcome_email, to_email, name, step, customer_id,
    )
