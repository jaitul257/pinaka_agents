"""Customer message classifier and AI draft generator.

Categorizes incoming messages into 7 types and generates draft responses
using Claude with product knowledge and customer memory context.

Uses AsyncAnthropic for non-blocking API calls.
"""

import logging
import re
from typing import Any

import anthropic

from src.core.settings import settings

logger = logging.getLogger(__name__)

MESSAGE_TYPES = [
    "product_question",
    "order_status",
    "sizing_question",
    "custom_request",
    "complaint",
    "return_request",
    "general_inquiry",
]

# Pre-filter patterns that skip LLM categorization
OBVIOUS_PATTERNS = {
    "order_status": [
        re.compile(r"(where|when).*(order|package|shipment|tracking)", re.I),
        re.compile(r"(track|shipped|deliver)", re.I),
    ],
}

SYSTEM_PROMPT = f"""You are the customer service voice for Pinaka Jewellery. You are warm,
personal, and genuinely helpful. You sound like a family jeweler who cares about making
the customer's milestone moment perfect.

Rules:
- NEVER reveal cost, margin, supplier, or wholesale pricing information
- NEVER mention AI, automation, or that this is a draft
- Always sign off with "Warm regards,\\n{settings.founder_name}"
- For sizing questions, be specific about bracelet fit and offer adjustments
- For complaints, acknowledge the issue first, then propose a solution
- Keep responses under 200 words unless the question requires detail
- If you don't know something, say "Let me check on that and get back to you"
  rather than guessing

Message categories: {', '.join(MESSAGE_TYPES)}"""


class MessageClassifier:
    """Classify and draft responses for customer messages."""

    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def classify(self, message: str) -> str:
        """Classify a message into one of 7 types. Uses regex pre-filter for obvious cases."""
        for msg_type, patterns in OBVIOUS_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(message):
                    logger.debug("Pre-filtered message as %s", msg_type)
                    return msg_type

        response = await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=50,
            messages=[
                {
                    "role": "user",
                    "content": f"""Classify this customer message into exactly one category.
Categories: {', '.join(MESSAGE_TYPES)}

Message: "{message}"

Reply with ONLY the category name, nothing else.""",
                }
            ],
        )

        result = response.content[0].text.strip().lower()
        if result in MESSAGE_TYPES:
            return result
        return "general_inquiry"

    async def draft_response(
        self,
        customer_message: str,
        category: str,
        product_context: str = "",
        order_context: str = "",
        customer_context: str = "",
    ) -> str:
        """Generate an AI draft response for founder review.

        customer_context includes: name, order count, LTV, lifecycle stage, notes.
        The AI uses this to personalize responses (e.g., "Welcome back, Sarah").

        Phase 12.5b: if 10+ founder edits have accumulated for this trigger
        type and the Sunday cron has rolled them into a founder_style rule,
        that rule is appended to the system prompt so drafts match Jaitul's
        actual voice without us hand-tuning the prompt.
        """
        context_parts = [f"Message category: {category}"]
        if customer_context:
            context_parts.append(f"Customer history:\n{customer_context}")
        if product_context:
            context_parts.append(f"Product knowledge:\n{product_context}")
        if order_context:
            context_parts.append(f"Order details:\n{order_context}")

        context = "\n\n".join(context_parts)
        system = await _augment_with_founder_style(
            SYSTEM_PROMPT, agent_name="customer_service",
            trigger_type="customer_response",
        )

        response = await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": f"""{context}

Customer message: "{customer_message}"

Draft a response:""",
                }
            ],
        )

        return response.content[0].text.strip()

    def is_urgent(self, category: str, message: str) -> bool:
        """Determine if a message should be flagged as urgent."""
        if category == "complaint":
            return True
        urgent_keywords = ["damaged", "broken", "wrong", "missing", "refund", "dispute", "angry"]
        message_lower = message.lower()
        return any(word in message_lower for word in urgent_keywords)


async def _augment_with_founder_style(
    base: str, agent_name: str, trigger_type: str,
) -> str:
    """Wrapper to keep the classifier's Anthropic-call dependency on
    feedback_loop soft — if the loop module is missing or the DB is
    unreachable, drafts still generate from the base prompt."""
    try:
        from src.agents.feedback_loop import augment_system_prompt
        return await augment_system_prompt(base, agent_name, trigger_type)
    except Exception:
        return base
