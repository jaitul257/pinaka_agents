"""Storefront AI Concierge — product discovery chat for pinakajewellery.com.

Uses Shopify's Storefront MCP to search products and Claude to provide
warm, consultative responses. Designed for embedding as a chat widget
on the storefront.

The concierge can:
- Search products by natural language query
- Answer questions about materials, sizing, care
- Recommend products based on occasion/budget
- Look up store policies (shipping, returns)
"""

import json
import logging
from typing import Any

import anthropic
import httpx

from src.core.settings import settings

logger = logging.getLogger(__name__)

MCP_ENDPOINT = f"https://{settings.shopify_shop_domain}/api/mcp"

SYSTEM_PROMPT = """You are the AI concierge for Pinaka Jewellery, a premium handcrafted \
diamond tennis bracelet brand. You help customers discover the perfect bracelet.

PERSONALITY:
- Warm, knowledgeable, and genuinely helpful — like a trusted family jeweler.
- Never pushy or salesy. Let the product quality speak for itself.
- Knowledgeable about diamonds, metals, and craftsmanship.
- Sign off as "Your Pinaka Concierge" when appropriate.

PRODUCT KNOWLEDGE:
- All bracelets are handcrafted, made-to-order (15 business days).
- Available metals: Yellow Gold, White Gold, Rose Gold.
- Available wrist sizes: 6", 6.5", 7", 7.5".
- Diamonds: Lab-grown, VS1-VS2 clarity, F-G color, round brilliant cut.
- Each stone is set by hand under 10x magnification.
- Free insured shipping on every order.

SIZING GUIDANCE:
- Measure wrist with a soft tape measure or string.
- Add 0.5-1" for comfortable fit.
- Most women: 6.5" or 7". Most men: 7.5" or 8".
- When in doubt, recommend 7" (most popular).

RULES:
- Never discuss competitor brands.
- Never reveal wholesale pricing, margins, or supplier details.
- Never mention AI or that you are automated.
- If asked about custom orders, say "Email us at hello@pinakajewellery.com for custom requests."
- Keep responses under 150 words unless the question requires detail.
"""


class StorefrontConcierge:
    """Chat endpoint for product discovery on the storefront."""

    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def chat(
        self,
        message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Process a customer chat message and return a response.

        Args:
            message: The customer's message.
            conversation_history: Previous messages in the conversation
                [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]

        Returns:
            {"response": str, "products": list[dict], "suggested_questions": list[str]}
        """
        # Search for products if the message seems product-related
        products = []
        if self._should_search(message):
            products = await self._search_products(message)

        # Build messages for Claude
        messages = list(conversation_history or [])

        # Add product context to the user message if we found products
        user_content = message
        if products:
            product_context = "\n".join(
                f"- {p['title']} (${p['price']}) — {p['url']}"
                for p in products[:5]
            )
            user_content = f"{message}\n\n[Available products matching this query:\n{product_context}]"

        messages.append({"role": "user", "content": user_content})

        # Call Claude
        response = await self._client.messages.create(
            model=settings.claude_model,
            system=SYSTEM_PROMPT,
            max_tokens=300,
            messages=messages,
        )

        reply = response.content[0].text.strip()

        # Generate follow-up suggestions
        suggestions = self._suggest_followups(message, products)

        return {
            "response": reply,
            "products": products[:3],
            "suggested_questions": suggestions,
        }

    @staticmethod
    def _should_search(message: str) -> bool:
        """Decide if we should search the product catalog for this message."""
        search_signals = [
            "bracelet", "diamond", "gold", "price", "cost", "buy",
            "shop", "product", "collection", "recommend", "gift",
            "anniversary", "birthday", "wedding", "size", "metal",
            "rose gold", "white gold", "yellow gold", "tennis",
            "show me", "what do you have", "looking for",
        ]
        msg_lower = message.lower()
        return any(signal in msg_lower for signal in search_signals)

    async def _search_products(self, query: str) -> list[dict[str, Any]]:
        """Search the Shopify catalog via MCP endpoint."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    MCP_ENDPOINT,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "search_catalog",
                            "arguments": {
                                "query": query,
                                "context": "Customer browsing the store, looking for jewellery",
                                "limit": 5,
                            },
                        },
                    },
                )

                if resp.status_code != 200:
                    logger.warning("MCP search returned HTTP %s", resp.status_code)
                    return []

                data = resp.json()
                result = data.get("result", {})
                if result.get("isError"):
                    logger.warning("MCP returned error: %s", result)
                    return []

                content = result.get("content", [])
                if not content:
                    return []

                catalog_data = json.loads(content[0].get("text", "{}"))
                products = catalog_data.get("products", [])

                return [
                    {
                        "title": p.get("title", ""),
                        "price": self._extract_price(p),
                        "url": p.get("url", ""),
                        "image": self._extract_image(p),
                        "product_id": p.get("id", ""),
                        "options": [
                            opt.get("name", "") for opt in p.get("options", [])
                        ],
                    }
                    for p in products
                ]
        except Exception:
            logger.exception("MCP product search failed")
            return []

    @staticmethod
    def _extract_price(product: dict) -> str:
        """Extract min price from product. MCP returns amount in cents."""
        price_range = product.get("price_range", {})
        min_price = price_range.get("min", {})
        if isinstance(min_price, dict):
            amount = min_price.get("amount", 0)
            try:
                dollars = float(amount) / 100
                return f"{dollars:,.0f}"
            except (ValueError, TypeError):
                return "0"
        return str(min_price) if min_price else "0"

    @staticmethod
    def _extract_image(product: dict) -> str:
        """Extract primary image URL from product media."""
        media = product.get("media", [])
        for m in media:
            if m.get("type") == "image" and m.get("url"):
                return m["url"]
        return ""

    @staticmethod
    def _suggest_followups(message: str, products: list) -> list[str]:
        """Generate contextual follow-up question suggestions."""
        suggestions = []
        msg_lower = message.lower()

        if products:
            suggestions.append("What sizes are available?")
            suggestions.append("Tell me about the diamond quality")
        if "size" not in msg_lower:
            suggestions.append("How do I choose the right wrist size?")
        if "ship" not in msg_lower and "deliver" not in msg_lower:
            suggestions.append("How long does shipping take?")
        if "care" not in msg_lower:
            suggestions.append("How do I care for my bracelet?")
        if "gift" not in msg_lower:
            suggestions.append("Is this a good anniversary gift?")

        return suggestions[:3]
