"""AI-powered Shopify product content generator using Claude.

Generates titles, descriptions, and tags from product data.
Shopify product descriptions support HTML. Titles have no hard limit
but should be concise for SEO (~70 chars visible in search).
"""

import logging
from typing import Any

import anthropic

from src.core.settings import settings
from src.product.schema import Product

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a luxury jewelry copywriter for Pinaka Jewellery, a premium brand
specializing in Diamond Tennis Bracelets. Your tone is warm, personal, and confident —
like a family jeweler who genuinely cares about the occasion the piece marks.

Rules:
- Never use the word "beautiful" (overused in jewelry)
- Never reveal cost, margin, or supplier information
- Always name the specific occasion when possible (anniversary, graduation, engagement)
- Sign off descriptions with a personal touch about handcrafted quality
- Focus the first 160 characters on what makes this piece special (SEO meta description)
- Made-to-order: mention "Ships in 10-15 business days" for handcrafted pieces

Format:
- Title: concise, keyword-rich, ~70 characters for SEO
- Description: 3 paragraphs (occasion/story, product details, care/certification)
- Tags: up to 15 relevant tags for Shopify search"""


class ListingGenerator:
    """Generate Shopify product content from product data."""

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def generate(self, product: Product, variant: str | None = None) -> dict[str, Any]:
        """Generate title, description, and tags for a Shopify product."""
        product_context = self._build_product_context(product, variant)

        response = self._client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"""Generate a Shopify product listing for this product:

{product_context}

Return your response in this exact format:
TITLE: [your title here]
DESCRIPTION: [your full description here]
TAGS: [tag1, tag2, tag3, ...]""",
                }
            ],
        )

        return self._parse_response(response.content[0].text)

    def _build_product_context(self, product: Product, variant: str | None) -> str:
        parts = [
            f"Product: {product.name}",
            f"Category: {product.category}",
            f"Metal: {product.materials.metal}",
            f"Total carat weight: {product.materials.total_carat}ct",
            f"Diamond type: {', '.join(product.materials.diamond_type)}",
            f"Story: {product.story}",
            f"Occasions: {', '.join(product.occasions)}",
            f"Fulfillment: Made-to-order, ships in {settings.made_to_order_days} business days",
        ]
        if variant and variant in product.pricing:
            parts.append(f"Retail price: ${product.pricing[variant].retail:,.2f}")
        if product.certification:
            parts.append(
                f"Certification: {product.certification.grading_lab} "
                f"#{product.certification.certificate_number}"
            )
        return "\n".join(parts)

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Parse Claude's response into structured listing data."""
        title = ""
        description = ""
        tags = []

        lines = text.strip().split("\n")
        current_section = None

        for line in lines:
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "").strip()
                current_section = "title"
            elif line.startswith("DESCRIPTION:"):
                description = line.replace("DESCRIPTION:", "").strip()
                current_section = "description"
            elif line.startswith("TAGS:"):
                tags_str = line.replace("TAGS:", "").strip()
                tags = [t.strip() for t in tags_str.split(",")]
                current_section = "tags"
            elif current_section == "description":
                description += "\n" + line

        return {
            "title": title,
            "description": description.strip(),
            "tags": tags,
        }
