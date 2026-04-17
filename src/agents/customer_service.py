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
handcrafted diamond tennis bracelet brand (~$5,000 AOV, made-to-order, \
15 business day lead time).

## Your job

Reply to one customer message per run. Start by understanding who is writing \
and what they need, then decide whether you can answer directly or whether \
the founder should. Err toward escalation when the data is ambiguous.

## Suggested process

1. `classify_message` to get category + confidence.
2. `lookup_customer` by email — check for prior complaints, return requests, \
   lifecycle stage, and last interaction.
3. If the message references an order, `lookup_order` for live status.
4. Decide: auto-respond, or escalate?

## Auto-respond — allowed ONLY when all of these are true

- Classifier returned one of: order_status, shipping_inquiry, \
  product_inquiry, product_question, sizing_question, general_inquiry.
- Classifier confidence is high (not medium or low).
- Customer has NO complaint/return/refund request in `past_interactions` \
  within the last 60 days.
- The data needed to answer is available (e.g. tracking exists for an \
  order_status question; if it doesn't, escalate — don't invent).

If any condition fails, escalate. An escalated question is always cheaper \
than a wrong auto-reply to an unhappy customer.

## Always escalate

- complaint: unhappy customers need a human voice. No exceptions.
- return_request / refund_request: policy decision.
- custom_request / custom_order: pricing decision.

## Tone & style

- Warm, personal, premium. A family jeweler, not a corporation.
- Sign off as "Warm regards, Jaitul at Pinaka Jewellery".
- Under 200 words unless the question genuinely needs more.
- Available sizes: 6", 6.5", 7", 7.5".

## Never

- Reveal margins, supplier info, or wholesale pricing.
- Mention AI, automation, or that responses are drafted.
- Promise faster than 15 business days for made-to-order pieces.
- Fabricate tracking numbers, ETAs, or order status.

## When auto-responding

Sequence: `draft_customer_reply` → (if uncertain) `review_email_draft` → \
`send_email` OR `post_to_slack`.

The cross-model `review_email_draft` is there for when you're not fully \
confident — customer context is ambiguous, tone is tricky, or you had to \
make an assumption. It returns a verdict from an independent model \
(GPT-4o-mini reviewing your Claude draft):

- `pass`   — send as-is.
- `revise` — rewrite once using the findings, then send. Do NOT call \
              `review_email_draft` on the revision — one review per draft.
- `block`  — do NOT send. Escalate to Slack with the findings.

Default confidence LOW if you had to make any assumption. Consider calling \
the reviewer whenever confidence would be LOW.
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
                "Read-only. Fetch structured customer row by email (case-insensitive). "
                "Returns {id, name, email, order_count, lifetime_value (USD), "
                "lifecycle_stage, last_order_at, accepts_marketing} or null. "
                "For qualitative context (past complaints, voice cues, open threads) "
                "call `get_entity_memory` separately — it's the compiled wiki note."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Customer email address"},
                },
                "required": ["email"],
            },
            func=self._db.get_customer_by_email,
            risk_tier=1,
        )

        self.tools.register(
            name="get_entity_memory",
            description=(
                "Read-only. Fetch the LLM-compiled wiki note for a specific "
                "customer, product SKU, or calendar month (entity_type = "
                "'customer' | 'product' | 'seasonal'). Returns markdown content "
                "plus compiled_at freshness timestamp, or null if no note exists "
                "yet. Short (500-800 words) distilled summary of the entity's "
                "history — orders, interactions, patterns, open threads for "
                "customers; sales + creative performance for products; YoY "
                "patterns for seasonal. Call this BEFORE drafting a customer "
                "reply so you know about prior complaints or voice cues. "
                "Do NOT call for cold lookups — classify first, then pull memory "
                "only when you need qualitative context."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["customer", "product", "seasonal"],
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Customer id (numeric string), SKU, or 'MM' for seasonal",
                    },
                },
                "required": ["entity_type", "entity_id"],
            },
            func=self._get_entity_memory_wrapper,
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

        self.tools.register(
            name="review_email_draft",
            description=(
                "Independent cross-model review of a customer email draft "
                "(GPT-4o-mini reviews Claude's draft). Returns "
                "{verdict: 'pass'|'revise'|'block', findings: list[str], "
                "rationale: str, review_id: int}. "
                "WHEN TO CALL: any time you are not fully confident in the "
                "draft — customer context is ambiguous, tone is tricky, "
                "you had to make an assumption, or the category is borderline. "
                "Call BEFORE send_email. If verdict is 'block', do NOT send — "
                "post_to_slack with the findings instead. If 'revise', you "
                "may rewrite ONCE and send (do not loop). If 'pass', send. "
                "Do not call for drafts you have already revised based on a "
                "prior review — one review per draft is enough."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "draft_text": {
                        "type": "string",
                        "description": "Full email body text you intend to send",
                    },
                    "context_snippet": {
                        "type": "string",
                        "description": "1-3 sentences on what the customer asked and what you assumed",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "Customer id or order id this email concerns",
                    },
                },
                "required": ["draft_text"],
            },
            func=self._review_email_draft_wrapper,
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

    async def _get_entity_memory_wrapper(
        self, entity_type: str, entity_id: str,
    ) -> dict | None:
        from src.agents.memory import get_memory
        note = await get_memory(entity_type, str(entity_id))
        if not note:
            return None
        # Return content + compiled_at so the agent knows if the wiki is
        # stale enough to warrant ignoring.
        return {
            "content": note.get("content"),
            "compiled_at": note.get("compiled_at"),
            "sample_count": note.get("sample_count"),
        }

    async def _review_email_draft_wrapper(
        self, draft_text: str, context_snippet: str = "",
        entity_id: str | None = None,
    ) -> dict:
        from src.agents.skeptic import review_customer_email_draft
        review = await review_customer_email_draft(
            draft_text=draft_text,
            context_snippet=context_snippet,
            action_type="customer_response",
            entity_type="customer" if entity_id else None,
            entity_id=entity_id,
        )
        return {
            "verdict": review.verdict,
            "findings": review.findings,
            "rationale": review.rationale,
            "review_id": review.review_id,
            "reviewer_model": "gpt-4o-mini",
        }
