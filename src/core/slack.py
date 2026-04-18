"""Slack Block Kit message builder and sender.

Handles all Slack notifications: customer response reviews, listing drafts,
budget changes, shipping/fraud alerts, morning digest, and weekly rollup.
"""

import json
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

    async def open_edit_modal(
        self,
        trigger_id: str,
        action_id: str,
        value: str,
        original_text: str,
        channel: str = "",
        message_ts: str = "",
    ) -> dict[str, Any]:
        """Open a prefilled text-edit modal (Phase 12.5a).

        `action_id` is one of edit_response / edit_cart_recovery /
        edit_crafting_update / edit_listing. Founder tweaks the text and
        submits; Slack posts a `view_submission` payload to /webhook/slack
        which routes to `_handle_slack_modal_submit`.

        `original_text` is both prefilled in the textarea AND stashed in
        private_metadata so the submit handler can diff without re-querying.
        """
        metadata = json.dumps({
            "value": str(value),
            # Slack caps private_metadata at 3000 chars. Originals longer
            # than that are truncated — the diff still captures edits on
            # the portion we kept.
            "original_text": original_text[:2600],
            "channel": channel,
            "message_ts": message_ts,
        })
        view = {
            "type": "modal",
            "callback_id": f"modal_{action_id}",
            "private_metadata": metadata,
            "title": {"type": "plain_text", "text": "Edit draft"},
            "submit": {"type": "plain_text", "text": "Send edited"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn",
                         "text": f":pencil2: Editing *{action_id}* for `#{value}`. "
                                 "Submit to send; your edits are captured for style learning."},
                    ],
                },
                {
                    "type": "input",
                    "block_id": "edited_text_block",
                    "label": {"type": "plain_text", "text": "Email body"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "edited_text",
                        "multiline": True,
                        "initial_value": (original_text or "")[:2900],
                        "max_length": 2900,
                    },
                },
            ],
        }
        response = await self._client.views_open(trigger_id=trigger_id, view=view)
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

    async def send_webhook_health_alert(
        self,
        re_registered: list[str],
        failed: list[str],
    ) -> dict[str, Any]:
        """Alert when webhook subscriptions were missing and auto-recovery was attempted."""
        status_lines = []
        for topic in re_registered:
            status_lines.append(f":white_check_mark: `{topic}` — re-registered")
        for topic in failed:
            status_lines.append(f":x: `{topic}` — FAILED to re-register")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":rotating_light: WEBHOOK HEALTH ALERT"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Shopify webhook subscriptions were missing (likely auto-deleted "
                        "after timeout failures). Auto-recovery results:\n\n"
                        + "\n".join(status_lines)
                    ),
                },
            },
        ]
        return await self.send_blocks(
            blocks, text=f"Webhook health: {len(re_registered)} restored, {len(failed)} failed"
        )

    async def send_refund_alert(
        self,
        order_number: str,
        refund_amount: float,
        reason: str,
        is_partial: bool,
    ) -> dict[str, Any]:
        """Alert when a refund is processed."""
        badge = "PARTIAL REFUND" if is_partial else "FULL REFUND"
        emoji = ":moneybag:" if is_partial else ":money_with_wings:"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {badge}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Order:* #{order_number}"},
                    {"type": "mrkdwn", "text": f"*Amount:* ${refund_amount:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Reason:* {reason or 'Not specified'}"},
                ],
            },
        ]
        return await self.send_blocks(
            blocks, text=f"{badge}: Order #{order_number}, ${refund_amount:,.2f}"
        )

    async def send_chargeback_evidence_ready(
        self,
        order_number: str,
        total: float,
        tracking_number: str,
        carrier: str,
        delivered_at: str,
    ) -> dict[str, Any]:
        """Notify that chargeback evidence has been collected for an order."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":shield: CHARGEBACK EVIDENCE READY"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Order:* #{order_number}"},
                    {"type": "mrkdwn", "text": f"*Total:* ${total:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Tracking:* {tracking_number} ({carrier})"},
                    {"type": "mrkdwn", "text": f"*Delivered:* {delivered_at}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Evidence package available at `GET /api/orders/{order_number}/evidence`",
                },
            },
        ]
        return await self.send_blocks(
            blocks, text=f"Chargeback evidence ready for Order #{order_number}"
        )

    async def send_lifecycle_email_review(
        self,
        customer_name: str,
        customer_email: str,
        customer_id: int,
        trigger: str,
        subject: str,
        email_body: str,
        context_note: str = "",
        anniversary_id: int | None = None,
        anniversary_year_key: str | None = None,
    ) -> dict[str, Any]:
        """Post a lifecycle email draft for founder approval."""
        icon = {
            "care_guide_day10": ":sparkles:",
            "referral_day60": ":gift:",
            "custom_inquiry_day180": ":pencil2:",
            "anniversary_year1": ":heart:",
        }.get(trigger, ":mailbox:")
        trigger_label = trigger.replace("_", " ").replace("day", "d ").replace("year", "y ").title()

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{icon} LIFECYCLE — {trigger_label}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name}"},
                    {"type": "mrkdwn", "text": f"*Email:* {customer_email}"},
                    {"type": "mrkdwn", "text": f"*Trigger:* `{trigger}`"},
                    {"type": "mrkdwn", "text": f"*Context:* {context_note or '—'}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Subject:* {subject}"},
            },
            {
                "type": "section",
                "block_id": "lifecycle_draft",
                "text": {"type": "mrkdwn", "text": f"*Draft:*\n{email_body}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"lifecycle_review_{customer_id}_{trigger}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve & Send"},
                        "style": "primary",
                        "action_id": "approve_lifecycle",
                        "value": json.dumps({
                            "customer_id": customer_id,
                            "customer_email": customer_email,
                            "customer_name": customer_name,
                            "trigger": trigger,
                            "subject": subject,
                            "anniversary_id": anniversary_id,
                            "anniversary_year_key": anniversary_year_key,
                        }),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Skip"},
                        "action_id": "skip_lifecycle",
                        "value": json.dumps({
                            "customer_id": customer_id,
                            "trigger": trigger,
                            "anniversary_id": anniversary_id,
                            "anniversary_year_key": anniversary_year_key,
                        }),
                    },
                ],
            },
        ]
        return await self.send_blocks(blocks, text=f"Lifecycle ({trigger}) for {customer_name}")

    async def send_reorder_reminder_review(
        self,
        customer_name: str,
        customer_email: str,
        last_order_number: str,
        last_order_total: float,
        days_since: int,
        email_draft: str,
        customer_id: int,
    ) -> dict[str, Any]:
        """Post a reorder reminder draft for founder review."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":gift: REORDER REMINDER REVIEW"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Customer:* {customer_name}"},
                    {"type": "mrkdwn", "text": f"*Email:* {customer_email}"},
                    {"type": "mrkdwn", "text": f"*Last Order:* #{last_order_number} (${last_order_total:,.2f})"},
                    {"type": "mrkdwn", "text": f"*Days Since:* {days_since}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "block_id": "reorder_draft",
                "text": {"type": "mrkdwn", "text": f"*AI Draft:*\n{email_draft}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"reorder_review_{customer_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve & Send"},
                        "style": "primary",
                        "action_id": "approve_reorder",
                        "value": json.dumps({
                            "customer_id": customer_id,
                            "customer_email": customer_email,
                            "customer_name": customer_name,
                        }),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Skip"},
                        "action_id": "skip_reorder",
                        "value": str(customer_id),
                    },
                ],
            },
        ]
        return await self.send_blocks(
            blocks, text=f"Reorder reminder for {customer_name} ({days_since} days)"
        )

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
