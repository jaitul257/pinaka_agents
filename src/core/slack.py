"""Slack Block Kit message builder and sender.

Handles all Slack notifications: customer response reviews, listing drafts,
budget changes, shipping/fraud alerts, morning digest, and weekly rollup.
"""

import logging
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from src.core.settings import settings

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Send Block Kit messages to the founder's Slack channel."""

    def __init__(self):
        self._client = AsyncWebClient(token=settings.slack_bot_token)
        self._channel = settings.slack_channel_id

    async def send_blocks(
        self,
        blocks: list[dict[str, Any]],
        text: str = "",
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Send a Block Kit message."""
        response = await self._client.chat_postMessage(
            channel=channel or self._channel,
            blocks=blocks,
            text=text or "Pinaka notification",
        )
        return response.data

    async def update_message(
        self,
        channel: str,
        ts: str,
        blocks: list[dict[str, Any]],
        text: str = "",
    ) -> dict[str, Any]:
        """Update an existing message (tombstone pattern after action)."""
        response = await self._client.chat_update(
            channel=channel,
            ts=ts,
            blocks=blocks,
            text=text or "Updated",
        )
        return response.data

    # ── Message Templates ──

    async def send_customer_response_review(
        self,
        customer_name: str,
        order_ref: str,
        category: str,
        original_message: str,
        ai_draft: str,
        message_id: int,
        is_urgent: bool = False,
    ) -> dict[str, Any]:
        """Template 1: Customer Response Review (Module 6)."""
        header_text = "URGENT — CUSTOMER RESPONSE REVIEW" if is_urgent else "CUSTOMER RESPONSE REVIEW"
        header_emoji = ":rotating_light:" if is_urgent else ":speech_balloon:"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{header_emoji} {header_text}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name}"},
                    {"type": "mrkdwn", "text": f"*Order:* {order_ref}"},
                    {"type": "mrkdwn", "text": f"*Category:* {category}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Original message:*\n>{original_message}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*AI Draft:*\n{ai_draft}",
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"customer_review_{message_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve_response",
                        "value": str(message_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit"},
                        "action_id": "edit_response",
                        "value": str(message_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": "reject_response",
                        "value": str(message_id),
                    },
                ],
            },
        ]

        notify = "@channel " if is_urgent else ""
        return await self.send_blocks(blocks, text=f"{notify}Customer response review: {customer_name}")

    async def send_listing_review(
        self,
        title: str,
        description: str,
        tags: list[str],
        listing_draft_id: str,
    ) -> dict[str, Any]:
        """Template 2: Listing Draft Review (Module 2)."""
        tags_str = ", ".join(tags)
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":memo: LISTING DRAFT REVIEW"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Title:* {title}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Description:*\n{description[:500]}..."},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Tags:* {tags_str}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"listing_review_{listing_draft_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve & Publish"},
                        "style": "primary",
                        "action_id": "approve_listing",
                        "value": listing_draft_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit"},
                        "action_id": "edit_listing",
                        "value": listing_draft_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": "reject_listing",
                        "value": listing_draft_id,
                    },
                ],
            },
        ]
        return await self.send_blocks(blocks, text=f"Listing draft: {title}")

    async def send_fraud_alert(
        self,
        receipt_id: int,
        buyer_name: str,
        total: float,
        flag_reason: str,
        insurance_note: str = "",
    ) -> dict[str, Any]:
        """Template 4: Shipping / Fraud Alert (Module 3)."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":warning: FRAUD ALERT"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Order:* #{receipt_id}"},
                    {"type": "mrkdwn", "text": f"*Buyer:* {buyer_name}"},
                    {"type": "mrkdwn", "text": f"*Total:* ${total:,.2f}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Flag:* {flag_reason}"},
            },
        ]

        if insurance_note:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Insurance:* {insurance_note}"},
            })

        blocks.extend([
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"fraud_alert_{receipt_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve Shipment"},
                        "style": "primary",
                        "action_id": "approve_shipment",
                        "value": str(receipt_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Hold for Review"},
                        "action_id": "hold_order",
                        "value": str(receipt_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Cancel Order"},
                        "style": "danger",
                        "action_id": "cancel_order",
                        "value": str(receipt_id),
                    },
                ],
            },
        ])

        return await self.send_blocks(
            blocks, text=f"@channel FRAUD ALERT: Order #{receipt_id} — ${total:,.2f}"
        )

    async def send_alert(self, message: str, level: str = "info") -> dict[str, Any]:
        """Simple text alert for operational notifications."""
        emoji = {
            "info": ":information_source:",
            "warning": ":warning:",
            "error": ":x:",
            "success": ":white_check_mark:",
        }.get(level, ":bell:")

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{emoji} {message}"},
            },
        ]
        return await self.send_blocks(blocks, text=message)

    # ── New Shopify Templates ──

    async def send_new_order_alert(
        self,
        order_number: str,
        customer_name: str,
        total: float,
        items: list[str],
    ) -> dict[str, Any]:
        """New order notification with key details."""
        items_text = ", ".join(items[:3])
        if len(items) > 3:
            items_text += f" +{len(items) - 3} more"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f":tada: New Order — ${total:,.2f}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Order:* #{order_number}"},
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name}"},
                    {"type": "mrkdwn", "text": f"*Items:* {items_text}"},
                ],
            },
        ]
        return await self.send_blocks(blocks, text=f"New order #{order_number}: ${total:,.2f}")

    async def send_customer_response_review_v2(
        self,
        customer_name: str,
        customer_email: str,
        category: str,
        original_message: str,
        ai_draft: str,
        message_id: int,
        urgency: str = "normal",
        customer_history: str = "",
    ) -> dict[str, Any]:
        """Customer service response review with customer memory context."""
        header_emoji = ":rotating_light:" if urgency == "urgent" else ":speech_balloon:"
        header_text = f"{header_emoji} Customer Inquiry — {category}"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*From:* {customer_name} ({customer_email})"},
                    {"type": "mrkdwn", "text": f"*Urgency:* {urgency}"},
                ],
            },
        ]

        # Customer history BEFORE draft (so founder can verify AI used context)
        if customer_history:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_history}"},
                ],
            })
        else:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "*Customer:* New lead — no purchase history"},
                ],
            })

        blocks.extend([
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Original Message:*\n>{original_message}"},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*AI Draft Response:*\n{ai_draft}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"customer_review_{message_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve & Send"},
                        "style": "primary",
                        "action_id": "approve_response",
                        "value": str(message_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✏️ Edit"},
                        "action_id": "edit_response",
                        "value": str(message_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🚫 Reject"},
                        "style": "danger",
                        "action_id": "reject_response",
                        "value": str(message_id),
                    },
                ],
            },
        ])

        notify = "@channel " if urgency == "urgent" else ""
        return await self.send_blocks(blocks, text=f"{notify}Customer inquiry: {customer_name}")

    async def send_abandoned_cart_review(
        self,
        cart_value: float,
        customer_name: str,
        customer_context: str,
        product_names: list[str],
        time_since: str,
        email_subject: str,
        email_body: str,
        cart_event_id: int,
    ) -> dict[str, Any]:
        """Abandoned cart recovery email review."""
        items_text = ", ".join(product_names[:3])

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f":shopping_trolley: Abandoned Cart — ${cart_value:,.2f}"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name} ({customer_context})"},
                    {"type": "mrkdwn", "text": f"*Cart:* {items_text}"},
                    {"type": "mrkdwn", "text": f"*Abandoned:* {time_since} ago"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Draft Recovery Email:*\n>Subject: {email_subject}\n>\n>{email_body[:500]}",
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"cart_recovery_{cart_event_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve & Send"},
                        "style": "primary",
                        "action_id": "approve_cart_recovery",
                        "value": str(cart_event_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✏️ Edit"},
                        "action_id": "edit_cart_recovery",
                        "value": str(cart_event_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "⏭️ Skip"},
                        "action_id": "skip_cart_recovery",
                        "value": str(cart_event_id),
                    },
                ],
            },
        ]

        return await self.send_blocks(blocks, text=f"Abandoned cart: ${cart_value:,.2f}")

    async def send_crafting_update_review(
        self,
        order_number: str,
        customer_name: str,
        customer_email: str,
        product_name: str,
        days_since_order: int,
        email_body: str,
        order_id: int,
    ) -> dict[str, Any]:
        """Post-purchase crafting update email review."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f":envelope: Crafting Update — Order #{order_number}"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name} ({customer_email})"},
                    {"type": "mrkdwn", "text": f"*Order:* {product_name}, placed {days_since_order} days ago"},
                    {"type": "mrkdwn", "text": "*Status:* No follow-up sent yet"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Draft Update Email:*\n>{email_body[:500]}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"crafting_update_{order_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve & Send"},
                        "style": "primary",
                        "action_id": "approve_crafting_update",
                        "value": str(order_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✏️ Edit"},
                        "action_id": "edit_crafting_update",
                        "value": str(order_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "⏭️ Skip"},
                        "action_id": "skip_crafting_update",
                        "value": str(order_id),
                    },
                ],
            },
        ]

        return await self.send_blocks(blocks, text=f"Crafting update for order #{order_number}")

    async def send_shipping_update(
        self,
        order_number: str,
        customer_name: str,
        tracking_number: str,
        carrier: str,
        tracking_url: str = "",
    ) -> dict[str, Any]:
        """Shipping notification when a package ships."""
        tracking_text = f"<{tracking_url}|{tracking_number}>" if tracking_url else tracking_number
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":package: Order Shipped"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Order:* #{order_number}"},
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name}"},
                    {"type": "mrkdwn", "text": f"*Carrier:* {carrier}"},
                    {"type": "mrkdwn", "text": f"*Tracking:* {tracking_text}"},
                ],
            },
        ]
        return await self.send_blocks(blocks, text=f"Order #{order_number} shipped via {carrier}")

    async def send_delivery_exception(
        self,
        order_number: str,
        customer_name: str,
        tracking_number: str,
        exception_detail: str,
    ) -> dict[str, Any]:
        """Urgent alert when a delivery exception occurs."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":rotating_light: DELIVERY EXCEPTION"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Order:* #{order_number}"},
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name}"},
                    {"type": "mrkdwn", "text": f"*Tracking:* {tracking_number}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Issue:* {exception_detail}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"delivery_exception_{order_number}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Contact Customer"},
                        "style": "primary",
                        "action_id": "contact_customer_exception",
                        "value": str(order_number),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": "dismiss",
                        "value": str(order_number),
                    },
                ],
            },
        ]
        return await self.send_blocks(
            blocks, text=f"@channel DELIVERY EXCEPTION: Order #{order_number}"
        )

    @staticmethod
    def tombstone_blocks(action: str, detail: str, timestamp: str) -> list[dict[str, Any]]:
        """Generate tombstone blocks to replace a message after action is taken."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":white_check_mark: *{action}* at {timestamp}\n{detail}",
                },
            },
        ]
