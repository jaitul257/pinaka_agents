"""Cross-model skeptic — Phase 13.3.

Claude drafts a customer-facing message. When the drafter isn't fully
confident (confidence != 'high'), a DIFFERENT model (GPT-4o) reviews the
draft against an asymmetric rubric and issues one of three verdicts:

  PASS    — send as-is
  REVISE  — draft has a fixable issue; drafter should revise once
  BLOCK   — escalate to founder, do not send

Why a different model:
  • Karpathy's LLM-Council (Nov 2025): models are surprisingly willing to
    select another model's output as superior to their own, making
    cross-model preference a real signal.
  • Kamoi TACL 2024: single-model self-critique without external ground
    truth often DEGRADES performance. Reflexion documented goal-drift
    where the critic hallucinates a new task spec.
  • We already have Anthropic. Using OpenAI for the reviewer is a cheap
    architectural choice that buys independent signal.

Why asymmetric scoring:
  • Symmetric critic = "reject to look useful" tendency
  • Asymmetric (+5 catch / −10 false-block) = critic has to be confident
    it found something real before saying BLOCK

The rubric goes in the PROMPT to the reviewer. GPT self-reports its score.
We don't actually pay out tokens — but the scoring frame reshapes its
behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.database import Database
from src.core.settings import settings

logger = logging.getLogger(__name__)


REVIEWER_MODEL = "gpt-4o-mini"  # cheap + fast; upgrade to gpt-4o if signal is weak
REVIEWER_MAX_TOKENS = 500
REVIEWER_TIMEOUT_SECONDS = 25

DRAFTER_MODEL = "claude-sonnet-4-5-20250929"  # recorded for audit, not used here


SYSTEM_PROMPT = """You are a skeptical reviewer for customer-facing messages \
at Pinaka Jewellery, a premium handcrafted diamond tennis bracelet brand. \
You are NOT the drafter — you are an independent second pair of eyes.

## Scoring rubric (asymmetric — read carefully)

For each review you file:
  +5  you correctly catch a real issue (voice drift, fabrication, legally \
or reputationally risky phrasing, missing context the customer asked for, \
wrong order/pricing facts, overpromising lead time)
  −10 you reject a clean draft and cause an unnecessary founder review

This asymmetry matters. A spurious block wastes founder attention TWICE — \
once to re-read it, once to recover the delayed customer. Only BLOCK or \
REVISE when you are confident you've caught something the drafter should \
fix.

## Pinaka voice fundamentals

- Warm, personal, premium. A family jeweler — NOT a corporation.
- Sign-off is "Warm regards, Jaitul at Pinaka Jewellery."
- Never: mention AI / automation / templates / scripts.
- Never: discount, mention margins/COGS, promise faster than 15 business days.
- Never: invent order numbers, tracking IDs, ETAs.
- Available sizes: 6", 6.5", 7", 7.5".
- Made-to-order lead time: 15 business days.

## Output format (strict JSON)

Respond with ONLY this JSON object, no preamble:

{
  "verdict": "pass" | "revise" | "block",
  "findings": [
    "one short sentence per finding — cite specific text from the draft"
  ],
  "score": <integer in -10..+5, your self-assessment of whether catching/not catching was correct>,
  "rationale": "one sentence explaining the verdict"
}

Default to PASS unless a finding genuinely matters. An imperfect but \
acceptable draft is PASS. REVISE is for fixable issues. BLOCK is reserved \
for things no founder should let go out (fabrication, legal/policy \
violation, brand-damaging tone)."""


@dataclass
class SkepticReview:
    verdict: str           # "pass" | "revise" | "block"
    findings: list[str]
    score: float           # -10 .. +5
    rationale: str
    tokens_used: int
    review_id: int | None  # skeptic_reviews.id


async def review_customer_email_draft(
    draft_text: str,
    context_snippet: str = "",
    action_type: str = "customer_response",
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> SkepticReview:
    """Review a customer-facing email draft with GPT-4o-mini.

    Always returns a review (never raises). If OpenAI is unreachable or
    misconfigured, returns a soft PASS with a rationale saying so — agents
    should NOT block sending when the reviewer itself is down. The alternative
    (failing closed) would stop every email whenever OpenAI has a hiccup.
    """
    if not settings.openai_api_key:
        logger.warning("skeptic: OPENAI_API_KEY not set, returning soft PASS")
        return SkepticReview(
            verdict="pass", findings=[],
            score=0, rationale="skeptic unavailable (no key), passed by default",
            tokens_used=0, review_id=None,
        )

    user_msg = (
        f"CONTEXT:\n{context_snippet or '(none provided)'}\n\n"
        f"DRAFT TO REVIEW:\n{draft_text}"
    )

    payload = {
        "model": REVIEWER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": REVIEWER_MAX_TOKENS,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=REVIEWER_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception:
        logger.exception("skeptic: OpenAI call failed")
        return SkepticReview(
            verdict="pass", findings=[],
            score=0, rationale="skeptic call failed; passed by default",
            tokens_used=0, review_id=None,
        )

    if resp.status_code != 200:
        logger.warning("skeptic: OpenAI HTTP %d: %s", resp.status_code,
                       resp.text[:200])
        return SkepticReview(
            verdict="pass", findings=[],
            score=0, rationale=f"skeptic returned HTTP {resp.status_code}; passed by default",
            tokens_used=0, review_id=None,
        )

    data = resp.json()
    choice_text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    tokens_used = int(data.get("usage", {}).get("total_tokens", 0))
    parsed = _parse_review(choice_text)

    # Persist for audit + dashboard + calibration measurement
    review_id = await _log_review(
        drafter_model=DRAFTER_MODEL,
        reviewer_model=REVIEWER_MODEL,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        draft_text=draft_text,
        context_snippet=context_snippet,
        verdict=parsed["verdict"],
        findings=parsed["findings"],
        score=parsed["score"],
        tokens_reviewer=tokens_used,
    )

    return SkepticReview(
        verdict=parsed["verdict"],
        findings=parsed["findings"],
        score=parsed["score"],
        rationale=parsed["rationale"],
        tokens_used=tokens_used,
        review_id=review_id,
    )


def _parse_review(raw: str) -> dict[str, Any]:
    """Extract the review JSON. Soft-fail to a PASS if the reviewer returns
    something un-parseable — we refuse to block sends over malformed output."""
    try:
        obj = json.loads(raw)
    except Exception:
        # Try to extract a JSON object from within text
        match = re.search(r"\{[\s\S]*\}", raw or "")
        if not match:
            return {"verdict": "pass", "findings": [],
                    "score": 0, "rationale": "reviewer output unparseable; soft pass"}
        try:
            obj = json.loads(match.group())
        except Exception:
            return {"verdict": "pass", "findings": [],
                    "score": 0, "rationale": "reviewer output unparseable; soft pass"}

    verdict = str(obj.get("verdict", "pass")).lower()
    if verdict not in ("pass", "revise", "block"):
        verdict = "pass"

    findings = obj.get("findings") or []
    if not isinstance(findings, list):
        findings = [str(findings)]
    findings = [str(f)[:300] for f in findings][:10]

    try:
        score = float(obj.get("score", 0))
    except Exception:
        score = 0.0
    score = max(-10.0, min(5.0, score))

    rationale = str(obj.get("rationale", ""))[:500]

    return {"verdict": verdict, "findings": findings,
            "score": score, "rationale": rationale}


async def _log_review(
    *,
    drafter_model: str,
    reviewer_model: str,
    action_type: str,
    entity_type: str | None,
    entity_id: str | None,
    draft_text: str,
    context_snippet: str,
    verdict: str,
    findings: list[str],
    score: float,
    tokens_reviewer: int,
) -> int | None:
    def _insert():
        sync = Database()
        return sync._client.table("skeptic_reviews").insert({
            "drafter_model": drafter_model,
            "reviewer_model": reviewer_model,
            "action_type": action_type,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "draft_text": draft_text[:4000],
            "context_snippet": (context_snippet or "")[:500],
            "verdict": verdict,
            "findings": findings,
            "score": score,
            "tokens_reviewer": tokens_reviewer,
        }).execute()
    try:
        res = await asyncio.to_thread(_insert)
        if res.data:
            return int(res.data[0]["id"])
    except Exception:
        logger.exception("skeptic: failed to log review")
    return None


# ── Dashboard + metric helpers ──

async def recent_reviews(limit: int = 50) -> list[dict[str, Any]]:
    def _q():
        sync = Database()
        return (sync._client.table("skeptic_reviews")
                .select("id,created_at,action_type,entity_type,entity_id,"
                        "verdict,findings,score,overridden_by_founder,reviewer_model")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()).data or []
    try:
        return await asyncio.to_thread(_q)
    except Exception:
        logger.exception("recent_reviews failed")
        return []


async def override_review(review_id: int, reason: str) -> bool:
    """Founder clicked 'override' on a BLOCK or REVISE — signal that the
    skeptic was wrong here. Used to calibrate whether the rubric is too harsh."""
    from datetime import datetime, timezone
    def _up():
        sync = Database()
        return (sync._client.table("skeptic_reviews")
                .update({
                    "overridden_by_founder": True,
                    "override_reason": reason[:500],
                    "override_at": datetime.now(timezone.utc).isoformat(),
                })
                .eq("id", review_id)
                .execute())
    try:
        res = await asyncio.to_thread(_up)
        return bool(res.data)
    except Exception:
        logger.exception("override_review failed for id=%s", review_id)
        return False


async def calibration_stats(days: int = 30) -> dict[str, Any]:
    """Stats founder can glance at to decide whether to tune the rubric."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def _q():
        sync = Database()
        return (sync._client.table("skeptic_reviews")
                .select("verdict,overridden_by_founder")
                .gte("created_at", since)
                .execute()).data or []
    try:
        rows = await asyncio.to_thread(_q)
    except Exception:
        logger.exception("calibration_stats failed")
        return {}

    total = len(rows)
    by_verdict = {"pass": 0, "revise": 0, "block": 0}
    overrides = {"pass": 0, "revise": 0, "block": 0}
    for r in rows:
        v = r.get("verdict") or "pass"
        by_verdict[v] = by_verdict.get(v, 0) + 1
        if r.get("overridden_by_founder"):
            overrides[v] = overrides.get(v, 0) + 1

    return {
        "total": total,
        "pass": by_verdict["pass"],
        "revise": by_verdict["revise"],
        "block": by_verdict["block"],
        "overridden_block": overrides["block"],
        "overridden_revise": overrides["revise"],
        # Higher ratio → rubric too aggressive → tune prompt or raise confidence gate
        "block_override_rate_pct": (overrides["block"] / by_verdict["block"] * 100
                                    if by_verdict["block"] else 0.0),
    }
