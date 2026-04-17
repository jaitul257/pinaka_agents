"""Post-purchase lifecycle orchestrator (Phase 9.2).

Four time-based triggers that compound every purchase into 4 future
touchpoints. Each trigger Claude-drafts a personal email, posts to Slack
for founder approval, and (on approve) sends via SendGrid.

Triggers:
  - care_guide_day10       : 10 days after purchase — care instructions
  - referral_day60         : 60 days after purchase — $250 credit referral
  - custom_inquiry_day180  : 180 days after purchase — "want a custom piece?"
  - anniversary_year1      : 7-30 days before captured anniversary — personal
                             note, triggered from customer_anniversaries

Dedup:
  - Per-customer + per-trigger via customers.lifecycle_emails_sent JSONB
  - Per-anniversary per-year via customer_anniversaries.reminded JSONB
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)


# ── Trigger definitions ──

CARE_GUIDE_DAYS = 10
REVIEW_REQUEST_DAYS = 20     # Phase 10: ~5d post-delivery assuming 15-day made-to-order window
REFERRAL_DAYS = 60
CUSTOM_INQUIRY_DAYS = 180

TRIGGER_CARE = "care_guide_day10"
TRIGGER_REVIEW = "review_request_day20"
TRIGGER_REFERRAL = "referral_day60"
TRIGGER_CUSTOM = "custom_inquiry_day180"
TRIGGER_ANNIVERSARY = "anniversary_year1"

REFERRAL_CREDIT = 250  # $ — from Phase 9.1 retro: "fine-jewelry referrals need real money"
GOOGLE_REVIEW_URL = "https://g.page/r/pinaka-jewellery/review"  # founder to replace with actual place ID
TRUSTPILOT_REVIEW_URL = "https://www.trustpilot.com/review/pinakajewellery.com"

LIFECYCLE_SYSTEM_PROMPT = f"""You are drafting a personal follow-up email for Pinaka Jewellery \
from the founder, Jaitul. The customer bought a handcrafted diamond tennis bracelet \
($4,500-$5,100 AOV) and we want to deepen the relationship with a specific, timely touch.

Absolute rules:
- Sign off with "Warm,\\n{settings.founder_name}" — one name only.
- Under 130 words. Short sentences. No em dashes.
- Never say "as an AI", never reference automation.
- No discounts or promo codes unless explicitly asked.
- No "I hope this email finds you well" / "just wanted to check in" openers.
- Reference the specific trigger (first 10 days / 60 days / 180 days / anniversary) naturally.
- For anniversary trigger: acknowledge the date, not the product.
- For referral trigger: mention the ${REFERRAL_CREDIT} credit directly, no coy hinting.
- For custom inquiry: open a door, don't push a sale.

Write ONLY the email body. No subject line, no signature header, no HTML. Plain text. \
The template wrapper handles greeting + footer."""


@dataclass
class LifecycleCandidate:
    """One pending lifecycle email — a customer + trigger + context for drafting."""
    customer_id: int
    customer_email: str
    customer_name: str
    trigger: str
    days_since_purchase: int | None = None
    anniversary_date: str | None = None
    relationship: str | None = None
    anniversary_id: int | None = None
    anniversary_year_key: str | None = None
    last_order_items: str = ""
    last_order_number: str = ""
    last_order_total: float = 0.0


@dataclass
class DraftedEmail:
    """A lifecycle email ready for Slack approval."""
    candidate: LifecycleCandidate
    subject: str
    body: str


class LifecycleOrchestrator:
    """Find lifecycle candidates, draft emails, and hand them to the founder."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    # ── Candidate discovery ──

    async def find_all_candidates(self) -> list[LifecycleCandidate]:
        """Scan orders + anniversaries for anyone due for a lifecycle email today."""
        candidates: list[LifecycleCandidate] = []
        candidates.extend(await self._find_time_based(CARE_GUIDE_DAYS, TRIGGER_CARE))
        candidates.extend(await self._find_time_based(REVIEW_REQUEST_DAYS, TRIGGER_REVIEW))
        candidates.extend(await self._find_time_based(REFERRAL_DAYS, TRIGGER_REFERRAL))
        candidates.extend(await self._find_time_based(CUSTOM_INQUIRY_DAYS, TRIGGER_CUSTOM))
        candidates.extend(await self._find_anniversary())
        logger.info("Lifecycle: %d total candidates (across 5 triggers)", len(candidates))
        return candidates

    async def _find_time_based(
        self, days_since_purchase: int, trigger: str
    ) -> list[LifecycleCandidate]:
        """Orders placed ~days_since_purchase ago whose customer hasn't had this trigger yet."""
        orders = await self._db.get_lifecycle_candidates_from_orders(
            days_since_purchase=days_since_purchase, window_days=2,
        )
        out: list[LifecycleCandidate] = []
        for order in orders:
            customer = order.get("customers") or {}
            if not customer:
                continue
            sent_map = customer.get("lifecycle_emails_sent") or {}
            if trigger in sent_map:
                continue  # already sent
            email = customer.get("email") or order.get("buyer_email") or ""
            if not email:
                continue
            line_items = order.get("line_items") or []
            items_str = (
                ", ".join(li.get("title", "item") for li in line_items if isinstance(li, dict))
                if isinstance(line_items, list) else str(line_items or "")
            ) or "your bracelet"
            out.append(LifecycleCandidate(
                customer_id=customer["id"],
                customer_email=email,
                customer_name=customer.get("name") or order.get("buyer_name") or email,
                trigger=trigger,
                days_since_purchase=days_since_purchase,
                last_order_items=items_str,
                last_order_number=str(order.get("shopify_order_id") or ""),
                last_order_total=float(order.get("total") or 0),
            ))
        return out

    async def _find_anniversary(self) -> list[LifecycleCandidate]:
        """Anniversaries coming up in 7-30 days that haven't been reminded this year."""
        rows = await self._db.get_anniversary_candidates(trigger_year=1)
        out: list[LifecycleCandidate] = []
        for row in rows:
            customer = row.get("customers") or {}
            customer_id = row.get("customer_id") or customer.get("id")
            email = row.get("customer_email") or customer.get("email") or ""
            if not customer_id or not email:
                continue
            out.append(LifecycleCandidate(
                customer_id=customer_id,
                customer_email=email,
                customer_name=customer.get("name") or email,
                trigger=TRIGGER_ANNIVERSARY,
                anniversary_date=row.get("anniversary_date"),
                relationship=row.get("relationship"),
                anniversary_id=row.get("id"),
                anniversary_year_key=row.get("_year_key"),
            ))
        return out

    # ── Drafting ──

    async def draft(self, candidate: LifecycleCandidate) -> DraftedEmail:
        """Run Claude to draft the body for a single candidate."""
        if not self._claude:
            return DraftedEmail(
                candidate=candidate,
                subject=_default_subject(candidate),
                body=_fallback_body(candidate),
            )

        user_prompt = _build_user_prompt(candidate)
        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=400,
                system=LIFECYCLE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            body = response.content[0].text.strip()
        except Exception:
            logger.exception("Claude lifecycle draft failed for trigger=%s cust=%s",
                             candidate.trigger, candidate.customer_id)
            body = _fallback_body(candidate)

        return DraftedEmail(
            candidate=candidate,
            subject=_default_subject(candidate),
            body=body,
        )


# ── Subject lines + fallback bodies ──

def _default_subject(c: LifecycleCandidate) -> str:
    if c.trigger == TRIGGER_CARE:
        return "Caring for your bracelet"
    if c.trigger == TRIGGER_REVIEW:
        return "One small favor, if you have a moment"
    if c.trigger == TRIGGER_REFERRAL:
        return f"A ${REFERRAL_CREDIT} thank-you, yours to pass on"
    if c.trigger == TRIGGER_CUSTOM:
        return "One question about what's next"
    if c.trigger == TRIGGER_ANNIVERSARY:
        return "A date I remembered"
    return "From Pinaka"


def _fallback_body(c: LifecycleCandidate) -> str:
    """Used when Claude is unavailable. Boring but on-brand."""
    if c.trigger == TRIGGER_CARE:
        return (
            f"It has been about 10 days since your bracelet arrived. A quick note on care: "
            "wipe with a soft cloth after wear, store in the pouch it came in, and bring it to any jeweler "
            "once a year for a polish. That is it. It is built to last.\n\n"
            "If anything feels off, just reply. I read every one.\n\n"
            f"Warm,\n{settings.founder_name}"
        )
    if c.trigger == TRIGGER_REVIEW:
        return (
            f"Your bracelet has been with you for a few days now. If it lives up to what you hoped, "
            f"a short honest review helps people like you find us.\n\n"
            f"Google: {GOOGLE_REVIEW_URL}\n"
            f"Trustpilot: {TRUSTPILOT_REVIEW_URL}\n\n"
            "Either one helps. If it did not live up — reply and tell me. I will make it right.\n\n"
            f"Warm,\n{settings.founder_name}"
        )
    if c.trigger == TRIGGER_REFERRAL:
        return (
            f"Two months in. If you have a friend who would love a piece, I put together a ${REFERRAL_CREDIT} credit "
            "you can pass on. They get ${REFERRAL_CREDIT} off their first bracelet, and so do you toward whatever is next.\n\n"
            "Reply with their email and I will send it over.\n\n"
            f"Warm,\n{settings.founder_name}"
        )
    if c.trigger == TRIGGER_CUSTOM:
        return (
            "Six months later — I wanted to ask one thing. Is there a piece you have been sketching in your head "
            "that does not exist yet? We do limited custom work a few times a year and I always want to hear "
            "what people are imagining.\n\n"
            "No pressure. Reply if the answer is yes.\n\n"
            f"Warm,\n{settings.founder_name}"
        )
    if c.trigger == TRIGGER_ANNIVERSARY:
        date_str = c.anniversary_date or "the date"
        return (
            f"Your {c.relationship or 'anniversary'} is coming up on {date_str}. I just wanted you to know I remember.\n\n"
            "Hope the day is quiet, warm, and exactly what you want it to be.\n\n"
            f"Warm,\n{settings.founder_name}"
        )
    return f"Hi,\n\nThinking of you.\n\nWarm,\n{settings.founder_name}"


def _build_user_prompt(c: LifecycleCandidate) -> str:
    """Per-trigger context for Claude."""
    if c.trigger == TRIGGER_CARE:
        return (
            f"Trigger: {TRIGGER_CARE}\n"
            f"Customer: {c.customer_name}\n"
            f"Their bracelet: {c.last_order_items}\n"
            f"Days since delivery (roughly): {c.days_since_purchase}\n\n"
            "Write a short care-guide email. Tell them 2-3 specific things to do, 1 thing NOT to do. "
            "Warm, practical. Closes with an invitation to reply if anything is off."
        )
    if c.trigger == TRIGGER_REVIEW:
        return (
            f"Trigger: {TRIGGER_REVIEW}\n"
            f"Customer: {c.customer_name}\n"
            f"Their bracelet: {c.last_order_items}\n"
            f"Days since order: {c.days_since_purchase} (roughly 5 days after delivery)\n\n"
            f"Write a warm, low-pressure review request. Include BOTH of these exact URLs on separate lines: "
            f"Google: {GOOGLE_REVIEW_URL} | Trustpilot: {TRUSTPILOT_REVIEW_URL}. "
            "Add: 'If it did not live up, reply and tell me — I will make it right.' "
            "Under 120 words. No 'we would love a review' filler."
        )
    if c.trigger == TRIGGER_REFERRAL:
        return (
            f"Trigger: {TRIGGER_REFERRAL}\n"
            f"Customer: {c.customer_name}\n"
            f"Their bracelet: {c.last_order_items}\n"
            f"Days since delivery: {c.days_since_purchase}\n\n"
            f"Offer a ${REFERRAL_CREDIT} referral credit — their friend gets ${REFERRAL_CREDIT} off, "
            f"they get ${REFERRAL_CREDIT} toward whatever is next. CTA: reply with friend's email. "
            "No coupon codes, no landing pages — personal referral."
        )
    if c.trigger == TRIGGER_CUSTOM:
        return (
            f"Trigger: {TRIGGER_CUSTOM}\n"
            f"Customer: {c.customer_name}\n"
            f"Their bracelet: {c.last_order_items}\n"
            f"Days since delivery: {c.days_since_purchase}\n\n"
            "Ask one question: is there a piece they have been sketching in their head that does not exist "
            "yet? Pinaka does limited custom work. Low-pressure. Reply if yes."
        )
    if c.trigger == TRIGGER_ANNIVERSARY:
        return (
            f"Trigger: {TRIGGER_ANNIVERSARY}\n"
            f"Customer: {c.customer_name}\n"
            f"Anniversary date: {c.anniversary_date}\n"
            f"Relationship/occasion: {c.relationship or 'anniversary'}\n\n"
            "Their special date is ~2 weeks out. A personal note acknowledging the date — NOT a sales email, "
            "not pushing the bracelet they already own. Just a warm 'I remembered' moment."
        )
    return f"Customer: {c.customer_name}\nTrigger: {c.trigger}\nDraft a short warm note."
