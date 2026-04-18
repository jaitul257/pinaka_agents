"""SendGrid transactional email sender for Pinaka Jewellery.

Uses dynamic templates with handlebars variables. All emails go through
founder approval in Slack before sending.

Phase 13.1 — every agent-attributable send attaches custom_args:
  • agent_name         which of the 5 agents caused this email
  • action_type        mirrors outcomes/auto_sent_actions taxonomy
  • audit_log_id       links back to the specific agent run, when available
  • entity_type/id     what the email concerns (order / customer / message)

SendGrid echoes these in every event webhook payload, which
`src/agents/outcomes.py:record_sendgrid_events` reads to correlate
delivered/open/click/bounce back to the agent run that caused the email.
Without custom_args the outcome rows would be anonymous and useless.
"""

import logging
from typing import Any

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Category, CustomArg, Mail, To

from src.core.settings import settings

logger = logging.getLogger(__name__)


# Max length per SendGrid — custom_args are tiny, keep them stringified.
_CUSTOM_ARG_MAX_VALUE_LEN = 200


def _build_email_context(
    agent_name: str | None = None,
    action_type: str | None = None,
    audit_log_id: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
) -> dict[str, str] | None:
    """Compose a SendGrid custom_args dict from loose agent attribution fields.

    Returns None when no attribution is available — callers that don't know
    which agent/action caused the send should pass nothing, not empty strings.
    """
    out: dict[str, str] = {}
    if agent_name:
        out["agent_name"] = str(agent_name)[:_CUSTOM_ARG_MAX_VALUE_LEN]
    if action_type:
        out["action_type"] = str(action_type)[:_CUSTOM_ARG_MAX_VALUE_LEN]
    if audit_log_id:
        out["audit_log_id"] = str(audit_log_id)[:_CUSTOM_ARG_MAX_VALUE_LEN]
    if entity_type:
        out["entity_type"] = str(entity_type)[:_CUSTOM_ARG_MAX_VALUE_LEN]
    if entity_id is not None and entity_id != "":
        out["entity_id"] = str(entity_id)[:_CUSTOM_ARG_MAX_VALUE_LEN]
    return out or None


class EmailSender:
    """Send transactional emails via SendGrid dynamic templates."""

    def __init__(self):
        self._client = SendGridAPIClient(api_key=settings.sendgrid_api_key)
        self._from_email = settings.sendgrid_from_email
        self._from_name = settings.sendgrid_from_name

    def send(
        self,
        to_email: str,
        template_id: str,
        template_data: dict[str, Any],
        to_name: str = "",
        custom_args: dict[str, str] | None = None,
        category: str | None = None,
    ) -> bool:
        """Send a dynamic template email. Returns True on success.

        custom_args  — key/value pairs SendGrid echoes back in event webhooks.
                       Use `_build_email_context(agent_name=..., action_type=...)`
                       in calling code. Limited to short strings each
                       (<= ~200 chars); anything longer will be truncated by
                       the helper.
        category     — single category string (not a list). Used as a
                       fallback when custom_args weren't set — SendGrid's
                       event payload always carries category, so
                       `outcomes._infer_agent_from_category` can still make a
                       best-effort guess (e.g. 'welcome_1' → retention).
        """
        message = Mail(
            from_email=(self._from_email, self._from_name),
            to_emails=To(to_email, to_name),
        )
        message.template_id = template_id
        message.dynamic_template_data = template_data

        if custom_args:
            for key, value in custom_args.items():
                try:
                    message.add_custom_arg(CustomArg(key, str(value)))
                except Exception:
                    # Never let a tagging bug block an email from going out
                    logger.exception("custom_arg %s failed to attach", key)

        if category:
            try:
                message.add_category(Category(category))
            except Exception:
                logger.exception("category %s failed to attach", category)

        try:
            response = self._client.send(message)
            logger.info(
                "Email sent to %s (template=%s, status=%d, agent=%s, action=%s)",
                to_email, template_id, response.status_code,
                (custom_args or {}).get("agent_name", "-"),
                (custom_args or {}).get("action_type", "-"),
            )
            return 200 <= response.status_code < 300
        except Exception:
            logger.exception("Failed to send email to %s (template=%s)", to_email, template_id)
            return False

    def send_cart_recovery(
        self,
        to_email: str,
        customer_name: str,
        cart_items: list[str],
        cart_value: float,
        customer_id: int | str | None = None,
        audit_log_id: str | None = None,
    ) -> bool:
        """Send abandoned cart recovery email."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_cart_recovery_template_id,
            template_data={
                "customer_name": customer_name,
                "cart_items": cart_items,
                "cart_value": f"${cart_value:,.2f}",
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
            custom_args=_build_email_context(
                agent_name="retention",
                action_type="cart_recovery",
                audit_log_id=audit_log_id,
                entity_type="customer" if customer_id else None,
                entity_id=customer_id,
            ),
            category="cart_recovery",
        )

    def send_crafting_update(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
        email_body: str,
        audit_log_id: str | None = None,
    ) -> bool:
        """Send post-purchase crafting update email."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_crafting_update_template_id,
            template_data={
                "customer_name": customer_name,
                "order_number": order_number,
                "email_body": email_body,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
            custom_args=_build_email_context(
                agent_name="order_ops",
                action_type="crafting_update_email",
                audit_log_id=audit_log_id,
                entity_type="order",
                entity_id=order_number,
            ),
            category="crafting_update",
        )

    def send_service_reply(
        self,
        to_email: str,
        customer_name: str,
        subject: str,
        email_body: str,
        customer_id: int | str | None = None,
        audit_log_id: str | None = None,
    ) -> bool:
        """Send customer service reply email."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_service_reply_template_id,
            template_data={
                "customer_name": customer_name,
                "subject": subject,
                "email_body": email_body,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
            custom_args=_build_email_context(
                agent_name="customer_service",
                action_type="customer_response",
                audit_log_id=audit_log_id,
                entity_type="customer" if customer_id else None,
                entity_id=customer_id,
            ),
            category="customer_service_reply",
        )

    def send_shipping_notification(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
        tracking_number: str,
        carrier: str,
        tracking_url: str = "",
    ) -> bool:
        """Send shipping notification email with tracking info."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_shipping_notification_template_id,
            template_data={
                "customer_name": customer_name,
                "order_number": order_number,
                "tracking_number": tracking_number,
                "carrier": carrier,
                "tracking_url": tracking_url,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
        )

    def send_delivery_confirmation(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
    ) -> bool:
        """Send delivery confirmation email."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_delivery_confirmation_template_id,
            template_data={
                "customer_name": customer_name,
                "order_number": order_number,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
        )

    def send_refund_confirmation(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
        refund_amount: float,
        is_partial: bool = False,
    ) -> bool:
        """Send refund confirmation email to customer."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_refund_confirmation_template_id,
            template_data={
                "customer_name": customer_name,
                "order_number": order_number,
                "refund_amount": f"${refund_amount:,.2f}",
                "is_partial": is_partial,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
        )

    def send_order_confirmation(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
        line_items: list[dict[str, Any]],
        total: float,
        shipping_address: str = "",
    ) -> bool:
        """Send order confirmation email after purchase."""
        items_summary = [
            {
                "title": item.get("title", "Item"),
                "quantity": item.get("quantity", 1),
                "price": item.get("price", "0"),
            }
            for item in line_items
        ]
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_order_confirmation_template_id,
            template_data={
                "customer_name": customer_name,
                "order_number": order_number,
                "items": items_summary,
                "total": f"${total:,.2f}",
                "shipping_address": shipping_address,
                "made_to_order_days": settings.made_to_order_days,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
        )

    def send_reorder_reminder(
        self,
        to_email: str,
        customer_name: str,
        email_body: str,
        customer_id: int | str | None = None,
        audit_log_id: str | None = None,
        interval_days: int | None = None,
    ) -> bool:
        """Send reorder reminder email to past customer."""
        # Use the same action_type values as approval_tiers.AUTO_ACTIONS so
        # outcomes + auto_sent_actions correlate cleanly.
        action_type = {
            180: "reorder_reminder_180d",
            365: "reorder_reminder_365d",
        }.get(interval_days or 0, "reorder_reminder")
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_reorder_reminder_template_id,
            template_data={
                "customer_name": customer_name,
                "email_body": email_body,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
            custom_args=_build_email_context(
                agent_name="retention",
                action_type=action_type,
                audit_log_id=audit_log_id,
                entity_type="customer" if customer_id else None,
                entity_id=customer_id,
            ),
            category=action_type,
        )

    def send_lifecycle_email(
        self,
        to_email: str,
        customer_name: str,
        subject: str,
        email_body: str,
        customer_id: int | str | None = None,
        trigger: str | None = None,
        audit_log_id: str | None = None,
    ) -> bool:
        """Send a post-purchase lifecycle email (care/referral/custom/anniversary).

        Uses a generic SendGrid template with {{subject}} + {{email_body}} vars,
        letting us reuse one template across all 4 lifecycle triggers.

        `trigger` should be one of the lifecycle trigger names
        (care_guide_day10, referral_day60, custom_inquiry_day180,
        anniversary_year1, review_request_day20) so events route to the
        right outcome bucket.
        """
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_lifecycle_template_id,
            template_data={
                "customer_name": customer_name,
                "subject": subject,
                "email_body": email_body,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
            custom_args=_build_email_context(
                agent_name="retention",
                action_type=trigger or "lifecycle_email",
                audit_log_id=audit_log_id,
                entity_type="customer" if customer_id else None,
                entity_id=customer_id,
            ),
            category=trigger or "lifecycle",
        )

    def send_welcome_email(
        self,
        to_email: str,
        customer_name: str,
        step: int,
        customer_id: int | str | None = None,
    ) -> bool:
        """Send a welcome series email (step 1-5).

        Uses five separate SendGrid templates (one per step) with static
        educational content. No Claude in the loop — pre-vetted copy.
        """
        template_id = {
            1: settings.sendgrid_welcome_1_template_id,
            2: settings.sendgrid_welcome_2_template_id,
            3: settings.sendgrid_welcome_3_template_id,
            4: settings.sendgrid_welcome_4_template_id,
            5: settings.sendgrid_welcome_5_template_id,
        }.get(step, "")
        if not template_id:
            logger.warning("No SendGrid template configured for welcome step %d", step)
            return False
        return self.send(
            to_email=to_email,
            template_id=template_id,
            template_data={
                "customer_name": customer_name,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
            custom_args=_build_email_context(
                agent_name="retention",
                action_type=f"lifecycle_welcome_{step}",
                entity_type="customer" if customer_id else None,
                entity_id=customer_id,
            ),
            category=f"welcome_{step}",
        )
