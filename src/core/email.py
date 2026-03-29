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
