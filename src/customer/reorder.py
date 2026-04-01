"""Predictive reorder reminder engine.

Finds customers who bought N days ago, drafts personalized AI reminders
suggesting complementary pieces, and routes through Slack for founder approval.
"""

import logging
from typing import Any

import anthropic

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)

REORDER_SYSTEM_PROMPT = f"""You are drafting a warm, personal reorder reminder email for Pinaka Jewellery.
The customer bought from us before and we want to suggest complementary pieces for their next milestone.

Rules:
- Mention the specific item they previously purchased
- Suggest 1-2 related items from the product catalog context provided
- Keep it warm and personal, not salesy
- Reference possible occasions (anniversary, birthday, gifting season)
- Keep the email under 150 words
- Sign off with "Warm regards,\\n{settings.founder_name}"
- NEVER mention AI, automation, discounts, or urgency tactics
- Do NOT include a subject line, just the email body"""


class ReorderEngine:
    """Find reorder candidates and draft personalized reminder emails."""

    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._db = AsyncDatabase()

    def _parse_reminder_days(self) -> list[int]:
        """Parse the comma-separated reminder days setting."""
        try:
            return [int(d.strip()) for d in settings.reorder_reminder_days.split(",") if d.strip()]
        except ValueError:
            logger.warning("Invalid reorder_reminder_days: %s, using defaults", settings.reorder_reminder_days)
            return [90, 180, 365]

    async def find_reorder_candidates(self) -> list[dict[str, Any]]:
        """Find customers eligible for reorder reminders across all day windows.

        Returns flat dicts with: customer_id, name, email, last_order_number,
        last_order_total, last_order_items, trigger_days.
        """
        reminder_days = self._parse_reminder_days()
        all_candidates = []

        for days in reminder_days:
            raw = await self._db.get_customers_for_reorder(
                days_since_purchase=days,
                cooldown_days=settings.reorder_cooldown_days,
            )
            for entry in raw:
                customer = entry.get("customer", {})
                last_order = entry.get("last_order", {})
                # Build line item summary from order
                line_items = last_order.get("line_items")
                if isinstance(line_items, list):
                    items_str = ", ".join(
                        li.get("title", "item") for li in line_items
                    )
                elif isinstance(line_items, str):
                    items_str = line_items
                else:
                    items_str = last_order.get("title", "jewelry purchase")

                all_candidates.append({
                    "customer_id": customer.get("id"),
                    "name": customer.get("name") or customer.get("buyer_name", "Customer"),
                    "email": customer.get("email", ""),
                    "last_order_number": str(last_order.get("shopify_order_id", "")),
                    "last_order_total": float(last_order.get("total", 0)),
                    "last_order_items": items_str or "jewelry purchase",
                    "trigger_days": days,
                })
            all_candidates.extend([])  # no-op, raw already processed

        # Deduplicate by customer_id (take earliest trigger)
        seen = set()
        unique = []
        for c in all_candidates:
            cid = c.get("customer_id")
            if cid and cid not in seen:
                seen.add(cid)
                unique.append(c)

        logger.info("Found %d reorder candidates across %d day windows", len(unique), len(reminder_days))
        return unique

    async def _get_product_context(self, last_order_items: str) -> str:
        """Get related product suggestions via ChromaDB, with Supabase fallback."""
        try:
            from src.product.embeddings import ProductEmbeddings
            embeddings = ProductEmbeddings()

            if embeddings.product_count() == 0:
                raise ValueError("ChromaDB empty")

            results = embeddings.query(last_order_items, n_results=3)
            if results:
                return "\n\n".join(r["document"] for r in results)
        except Exception:
            logger.warning("ChromaDB query failed for reorder context, falling back to Supabase")

        # Fallback: grab a few products from Supabase
        products = await self._db.get_all_products()
        if not products:
            return "No product catalog available."

        lines = []
        for p in products[:5]:
            name = p.get("title") or p.get("name", "")
            category = p.get("product_type") or p.get("category", "")
            price = p.get("price", "")
            lines.append(f"- {name} ({category}) ${price}")
        return "Available products:\n" + "\n".join(lines)

    async def draft_reminder(
        self,
        customer_name: str,
        last_order_items: str,
        trigger_days: int,
        product_context: str = "",
    ) -> str:
        """Use Claude to draft a personalized reorder reminder email."""
        if not product_context:
            product_context = await self._get_product_context(last_order_items)

        user_prompt = f"""Customer: {customer_name}
Their last purchase ({trigger_days} days ago): {last_order_items}

Related products from our catalog:
{product_context}

Draft a reorder reminder email:"""

        response = await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            system=REORDER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        return response.content[0].text.strip()
