"""Customer Service Agent — handles inbound customer messages autonomously.

Auto-responds to low-risk categories (order_status, shipping_inquiry,
product_inquiry). Always escalates complaints, refund/return requests,
and custom orders to Slack for founder review.
"""

from src.agents.base import CONFIDENCE_INSTRUCTIONS, BaseAgent
from src.agents.tools import ToolRegistry
from src.core.database import AsyncDatabase
from src.core.email import EmailSender
from src.core.settings import settings
from src.core.slack import SlackNotifier
from src.customer.classifier import MessageClassifier
from src.shipping.processor import ShippingProcessor

SYSTEM_PROMPT = """You are the Customer Service Agent for Pinaka Jewellery, a premium \
handcrafted diamond tennis bracelet brand.

For each inbound customer message, follow this process:
1. Classify the message type using classify_message.
2. Look up the customer using lookup_customer (by their email).
3. If an order is mentioned or relevant, look it up with lookup_order.
4. Based on the category, decide your action:

AUTO-RESPOND (you can handle these without human approval):
- order_status: Look up tracking with get_tracking_info and provide a clear status update.
- shipping_inquiry: Provide estimated delivery based on tracking or standard 15-day lead time.
- product_inquiry / product_question: Use search_products to find relevant items, draft a helpful response.
- sizing_question: Provide wrist size guidance (available sizes: 6", 6.5", 7", 7.5").
- general_inquiry: Draft a helpful response based on available context.

ALWAYS ESCALATE (post to Slack, do NOT send an email):
- complaint: Never auto-respond to unhappy customers. Escalate immediately.
- return_request / refund_request: Requires founder decision on refund policy.
- custom_request / custom_order: Requires pricing decision from founder.

TONE & STYLE:
- Warm, personal, premium. You represent a family jeweler, not a corporation.
- Sign off as "Warm regards, Jaitul at Pinaka Jewellery"
- Keep responses under 200 words unless the question needs detail.
- Never reveal margins, supplier info, or wholesale pricing.
- Never mention AI, automation, or that responses are drafted by a system.

When auto-responding:
1. First draft the response using draft_customer_reply.
2. Then send it using send_email.
""" + CONFIDENCE_INSTRUCTIONS


class CustomerServiceAgent(BaseAgent):
    """Handles inbound customer messages with auto-respond or escalation."""

    name = "customer_service"
    system_prompt = SYSTEM_PROMPT
    max_turns = 10

    def __init__(self):
        self._db = AsyncDatabase()
        self._classifier = MessageClassifier()
        self._email = EmailSender()
        self._shipping = ShippingProcessor()
        self._slack_notifier = SlackNotifier()
        super().__init__()

    def _register_tools(self):
        """Register customer service tools."""

        self.tools.register(
            name="classify_message",
            description=(
                "Categorize a customer message into one of 7 types: product_question, "
                "order_status, sizing_question, custom_request, complaint, return_request, "
                "general_inquiry. Uses regex pre-filter for obvious cases, Claude for ambiguous ones."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The customer's message text"},
                },
                "required": ["message"],
            },
            func=self._classifier.classify,
            risk_tier=1,
        )

        self.tools.register(
            name="lookup_customer",
            description=(
                "Get customer profile by email. Returns name, order count, lifetime value, "
                "lifecycle stage, last order date. Check this before any customer communication."
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
            name="lookup_order",
            description=(
                "Look up order details by Shopify order ID. Returns total, status, "
                "tracking number, shipping address, customer email."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "Shopify order ID"},
                },
                "required": ["order_id"],
            },
            func=self._lookup_order_wrapper,
            risk_tier=1,
        )

        self.tools.register(
            name="get_tracking_info",
            description=(
                "Fetch current tracking status for a shipment from ShipStation. "
                "Returns carrier, tracking number, ship date, delivery date."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "shipstation_order_id": {
                        "type": "integer",
                        "description": "The ShipStation order ID (stored on our order record)",
                    },
                },
                "required": ["shipstation_order_id"],
            },
            func=self._shipping.get_tracking,
            risk_tier=1,
        )

        self.tools.register(
            name="search_products",
            description=(
                "Search the product catalog using natural language. Returns top matching "
                "products with names, descriptions, prices, materials. Use for product "
                "recommendations and inquiries."
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
            name="draft_customer_reply",
            description=(
                "Generate a brand-voice customer reply using AI. Pass the original message, "
                "its category, and any context. Returns draft text. Does NOT send the email — "
                "you must call send_email separately after reviewing the draft."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "customer_message": {"type": "string", "description": "The original customer message"},
                    "category": {"type": "string", "description": "The message category from classify_message"},
                    "product_context": {"type": "string", "description": "Product info for context (optional)"},
                    "order_context": {"type": "string", "description": "Order details for context (optional)"},
                    "customer_context": {"type": "string", "description": "Customer history for context (optional)"},
                },
                "required": ["customer_message", "category"],
            },
            func=self._classifier.draft_response,
            risk_tier=2,
        )

        self.tools.register(
            name="send_email",
            description=(
                "Send an email to a customer. IRREVERSIBLE. Requires to_email, customer_name, "
                "subject, and email_body. Only call this after drafting and reviewing the content. "
                "Do NOT call this for complaints, refunds, or return requests — escalate those instead."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Recipient email address"},
                    "customer_name": {"type": "string", "description": "Recipient's name"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "email_body": {"type": "string", "description": "Email body text"},
                },
                "required": ["to_email", "customer_name", "subject", "email_body"],
            },
            func=self._send_email_wrapper,
            risk_tier=3,
        )

        self.tools.register(
            name="post_to_slack",
            description=(
                "Post a message to the Slack ops channel for human review. Use this to "
                "escalate complaints, refund requests, and custom orders. Include the "
                "customer's message and your recommended action."
            ),
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

    async def _lookup_order_wrapper(self, order_id: int) -> dict | None:
        return await self._db.get_order_by_shopify_id(order_id)

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

    def _send_email_wrapper(
        self, to_email: str, customer_name: str, subject: str, email_body: str
    ) -> dict:
        success = self._email.send_service_reply(
            to_email=to_email,
            customer_name=customer_name,
            subject=subject,
            email_body=email_body,
        )
        return {"sent": success, "to": to_email}

    async def _post_slack_wrapper(self, message: str) -> dict:
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": ":speech_balloon: Customer Service Agent"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Agent: {self.name} | Customer communication_"}]},
        ]
        await self._slack_notifier.send_blocks(blocks, text=message[:200])
        return {"posted": True}
