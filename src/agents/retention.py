"""Retention Agent — manages reorder reminders and cart recovery.

Reasons about timing based on customer history rather than just fixed
intervals. Respects email rate limits (2 cart recovery/week, 180-day
reorder cooldown) enforced by the PolicyEngine.
"""

from src.agents.base import BaseAgent
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.email import EmailSender
from src.core.settings import settings
from src.core.slack import SlackNotifier

SYSTEM_PROMPT = """You are the Retention Agent for Pinaka Jewellery, a premium \
handcrafted diamond tennis bracelet brand.

Your responsibilities:
1. Review customer order history and decide if a reorder reminder is appropriate.
2. For abandoned carts, decide if a cart recovery email should be sent.
3. Use product search to find complementary items for personalized recommendations.

REORDER REMINDERS:
- Check intervals: 90, 180, and 365 days after last purchase.
- 180-day minimum cooldown between reminder emails (enforced by guardrails).
- Personalize based on what they bought: suggest complementary pieces.
- Never send to customers who opted out of marketing.

CART RECOVERY:
- Only send after 60+ minutes of cart abandonment.
- Maximum 2 per customer per week (enforced by guardrails).
- Include the abandoned items and a personal touch.
- Don't send if the customer completed the purchase.

TONE:
- Warm and personal, not salesy. This is a luxury brand.
- Reference their previous purchase if applicable.
- Focus on the emotional value (milestones, gifts, self-reward).
"""


class RetentionAgent(BaseAgent):
    """Manages reorder reminders and cart recovery emails."""

    name = "retention"
    system_prompt = SYSTEM_PROMPT
    max_turns = 8

    def __init__(self):
        self._db = AsyncDatabase()
        self._email = EmailSender()
        self._slack_notifier = SlackNotifier()
        super().__init__()

    def _register_tools(self):

        self.tools.register(
            name="lookup_customer",
            description=(
                "Get customer profile by email. Returns name, order history, "
                "lifetime value, lifecycle stage, last_reorder_email_at."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Customer's email address"},
                },
                "required": ["email"],
            },
            func=self._db.get_customer_by_email,
            risk_tier=1,
        )

        self.tools.register(
            name="search_products",
            description=(
                "Search the product catalog for complementary items to recommend. "
                "Returns matching products with names, descriptions, prices."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "top_k": {"type": "integer", "description": "Number of results (default 3)"},
                },
                "required": ["query"],
            },
            func=self._search_products_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="send_reorder_reminder",
            description=(
                "Send a reorder reminder email to a past customer. Requires customer "
                "email, name, and email body. Subject to 180-day cooldown policy."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Customer's email"},
                    "customer_name": {"type": "string", "description": "Customer's name"},
                    "email_body": {"type": "string", "description": "Personalized email body"},
                },
                "required": ["to_email", "customer_name", "email_body"],
            },
            func=self._send_reorder_wrapper,
            risk_tier=3,
        )

        self.tools.register(
            name="send_cart_recovery",
            description=(
                "Send an abandoned cart recovery email. Maximum 2 per customer per week. "
                "Include the abandoned items and a personal touch."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Customer's email"},
                    "customer_name": {"type": "string", "description": "Customer's name"},
                    "cart_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of item names in the abandoned cart",
                    },
                    "cart_value": {"type": "number", "description": "Total cart value in dollars"},
                },
                "required": ["to_email", "customer_name", "cart_items", "cart_value"],
            },
            func=self._send_cart_recovery_wrapper,
            risk_tier=3,
        )

        self.tools.register(
            name="post_to_slack",
            description="Post retention updates to Slack.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message text for Slack"},
                },
                "required": ["message"],
            },
            func=self._post_slack_wrapper,
            risk_tier=1,
        )

    # ── Tool wrappers ──

    async def _search_products_wrapper(self, query: str, top_k: int = 3) -> list[dict]:
        try:
            from src.product.embeddings import ProductEmbeddings
            embeddings = ProductEmbeddings()
            results = embeddings.query(query, top_k=top_k)
            return [
                {"name": r.get("name", ""), "description": r.get("story", ""), "price": r.get("pricing", {}).get("base_price", "")}
                for r in results
            ]
        except Exception:
            return [{"error": "Product search not available"}]

    def _send_reorder_wrapper(self, to_email: str, customer_name: str, email_body: str) -> dict:
        success = self._email.send_reorder_reminder(
            to_email=to_email, customer_name=customer_name, email_body=email_body,
        )
        return {"sent": success, "to": to_email}

    def _send_cart_recovery_wrapper(
        self, to_email: str, customer_name: str, cart_items: list[str], cart_value: float
    ) -> dict:
        success = self._email.send_cart_recovery(
            to_email=to_email, customer_name=customer_name,
            cart_items=cart_items, cart_value=cart_value,
        )
        return {"sent": success, "to": to_email}

    async def _post_slack_wrapper(self, message: str) -> dict:
        blocks = [{
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":repeat: *Retention Agent*\n{message}"},
        }]
        await self._slack_notifier.send_blocks(blocks, text=message)
        return {"posted": True}
