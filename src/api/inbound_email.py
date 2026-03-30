"""SendGrid Inbound Parse webhook handler for customer service emails.

Receives incoming customer emails via SendGrid, classifies them using AI,
drafts a response, persists to Supabase, and posts to Slack for founder review.
"""

import logging
from typing import Any

from fastapi import HTTPException, Request

from src.core.database import Database
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.customer.classifier import MessageClassifier

logger = logging.getLogger(__name__)

# Categories where product knowledge improves the AI response
PRODUCT_CATEGORIES = {"product_question", "sizing_question", "custom_request"}

# Lazy singletons
_db = None
_slack = None
_classifier = None
_embeddings = None


def _get_db():
    global _db
    if _db is None:
        _db = Database()
    return _db


def _get_slack():
    global _slack
    if _slack is None:
        _slack = SlackNotifier()
    return _slack


def _get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = MessageClassifier()
    return _classifier


def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        from src.product.embeddings import ProductEmbeddings
        _embeddings = ProductEmbeddings()
    return _embeddings


async def handle_inbound_email(request: Request) -> dict[str, Any]:
    """Process an inbound email from SendGrid Inbound Parse.

    SendGrid posts multipart form data with fields:
    - from, to, subject, text, html, envelope, etc.
    """
    form = await request.form()

    sender_email = _extract_email(form.get("from", ""))
    subject = form.get("subject", "")
    body_text = form.get("text", "")
    body_html = form.get("html", "")

    # Prefer plain text, fall back to HTML stripped
    message_body = body_text.strip() if body_text else _strip_html(body_html)

    if not sender_email or not message_body:
        raise HTTPException(status_code=400, detail="Missing sender or body")

    logger.info("Inbound email from %s: %s", sender_email, subject[:50])

    # Look up customer
    customer = _get_db().get_customer_by_email(sender_email)
    customer_id = customer["id"] if customer else None
    customer_name = (customer.get("name") or sender_email) if customer else sender_email

    # Build customer context for AI
    customer_context = ""
    if customer:
        customer_context = (
            f"Name: {customer.get('name', 'Unknown')}, "
            f"Orders: {customer.get('order_count', 0)}, "
            f"LTV: ${float(customer.get('lifetime_value', 0)):,.2f}, "
            f"Stage: {customer.get('lifecycle_stage', 'unknown')}"
        )

    # Classify the message
    category = await _get_classifier().classify(message_body)
    is_urgent = _get_classifier().is_urgent(category, message_body)
    urgency = "urgent" if is_urgent else "normal"

    # Query product embeddings for product-related questions
    product_context = ""
    if category in PRODUCT_CATEGORIES:
        try:
            results = _get_embeddings().query(message_body, n_results=3)
            if results:
                product_context = "\n\n".join(r["document"] for r in results)
                logger.info("Found %d product matches for message", len(results))
        except Exception:
            logger.exception("Product embedding query failed, continuing without")

    # Draft AI response
    draft = await _get_classifier().draft_response(
        customer_message=message_body,
        category=category,
        product_context=product_context,
        customer_context=customer_context,
    )

    # Persist to Supabase
    message_record = _get_db().create_message({
        "customer_id": customer_id,
        "customer_email": sender_email,
        "buyer_name": customer_name,
        "subject": subject,
        "body": message_body,
        "category": category,
        "urgency": urgency,
        "ai_draft": draft,
        "direction": "inbound",
        "status": "pending_review",
    })

    message_id = message_record.get("id", 0)

    # Post to Slack for founder review
    await _get_slack().send_customer_response_review_v2(
        customer_name=customer_name,
        customer_email=sender_email,
        category=category,
        original_message=message_body[:500],
        ai_draft=draft,
        message_id=message_id,
        urgency=urgency,
        customer_history=customer_context,
    )

    logger.info(
        "Inbound email processed: %s from %s, category=%s, urgent=%s, message_id=%s",
        subject[:30], sender_email, category, is_urgent, message_id,
    )

    return {"status": "ok", "message_id": message_id, "category": category}


def _extract_email(from_field: str) -> str:
    """Extract email address from a 'Name <email>' or plain email string."""
    if "<" in from_field and ">" in from_field:
        start = from_field.index("<") + 1
        end = from_field.index(">")
        return from_field[start:end].strip().lower()
    return from_field.strip().lower()


def _strip_html(html: str) -> str:
    """Basic HTML tag stripping for fallback when no plain text is available."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return text.strip()
