"""SendGrid transactional email sender for Pinaka Jewellery.

Uses dynamic templates with handlebars variables. All emails go through
founder approval in Slack before sending.
"""

import logging
from typing import Any

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, To

from src.core.settings import settings

logger = logging.getLogger(__name__)


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
    ) -> bool:
        """Send a dynamic template email. Returns True on success."""
        message = Mail(
            from_email=(self._from_email, self._from_name),
            to_emails=To(to_email, to_name),
        )
        message.template_id = template_id
        message.dynamic_template_data = template_data

        try:
            response = self._client.send(message)
            logger.info(
                "Email sent to %s (template=%s, status=%d)",
                to_email, template_id, response.status_code,
            )
            return 200 <= response.status_code < 300
        except Exception:
            logger.exception("Failed to send email to %s (template=%s)", to_email, template_id)
            return False

    def send_cart_recovery(
        self, to_email: str, customer_name: str, cart_items: list[str], cart_value: float
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
        )

    def send_crafting_update(
        self,
        to_email: str,
        customer_name: str,
        order_number: str,
        email_body: str,
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
        )

    def send_service_reply(
        self, to_email: str, customer_name: str, subject: str, email_body: str
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
    ) -> bool:
        """Send reorder reminder email to past customer."""
        return self.send(
            to_email=to_email,
            template_id=settings.sendgrid_reorder_reminder_template_id,
            template_data={
                "customer_name": customer_name,
                "email_body": email_body,
                "founder_name": settings.founder_name,
            },
            to_name=customer_name,
        )

    def send_lifecycle_email(
        self,
        to_email: str,
        customer_name: str,
        subject: str,
        email_body: str,
    ) -> bool:
        """Send a post-purchase lifecycle email (care/referral/custom/anniversary).

        Uses a generic SendGrid template with {{subject}} + {{email_body}} vars,
        letting us reuse one template across all 4 lifecycle triggers.
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
        )

    def send_welcome_email(
        self,
        to_email: str,
        customer_name: str,
        step: int,
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
        )
