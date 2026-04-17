"""Pinterest API v5 client — create pins programmatically (Phase 9.3).

Pinterest is a visual search engine, not a social feed — perfect for
high-AOV jewelry where buyers discover via keyword + image. Posting 3
pins/week from our existing product photography + Claude-drafted
keyword-rich descriptions gives us long-term organic traffic at almost
zero cost.

Requires user to:
  1. Create a Pinterest Business account (free) at business.pinterest.com
  2. Create a dev app at developers.pinterest.com → apps → create
  3. Generate an access token with `pins:write` and `boards:read` scopes
  4. Paste on Railway: PINTEREST_ACCESS_TOKEN + PINTEREST_BOARD_ID

No-op gracefully if credentials are missing — useful for dry-run dev.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)

PINTEREST_API = "https://api.pinterest.com/v5"

PIN_SYSTEM_PROMPT = """You write Pinterest pin titles + descriptions for Pinaka Jewellery's \
$4,500-$5,100 handcrafted diamond tennis bracelets.

Pinterest is a visual search engine — pins surface based on title, description, and alt text \
matching user queries. Your copy must be keyword-rich but readable.

For a given product, produce:
- title: 40-100 chars. Front-load the strongest keyword (e.g. "Diamond Tennis Bracelet for 10 Year Anniversary").
- description: 400-500 chars. Include 3-4 varied long-tail phrases naturally. Mention: carats, metal, made-to-order, price range. End with a soft CTA.
- alt_text: 125-150 chars. Describe the image for accessibility and extra SEO signal.

Rules:
- Never use hashtags (Pinterest deprecated them for pins).
- No emoji in title (hurts ranking).
- No clickbait ("you won't believe...").
- Stick to concrete product facts — carats, metal type, setting style, craftsmanship days.

Output strict JSON: {"title": "...", "description": "...", "alt_text": "..."}"""


@dataclass
class PinDraft:
    product_name: str
    product_url: str
    image_url: str
    title: str
    description: str
    alt_text: str
    # Populated after API call
    pin_id: str | None = None
    error: str | None = None


class PinterestClient:
    """Create pins via Pinterest API v5."""

    def __init__(self):
        self._token = settings.pinterest_access_token
        self._board_id = settings.pinterest_board_id
        self._db = AsyncDatabase()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._board_id)

    async def pick_product(self, day_index: int = 0) -> dict[str, Any] | None:
        """Round-robin through active products based on the day counter.

        day_index can be any stable integer (e.g. days since epoch). We modulo
        it against the product count to rotate through the catalog.
        """
        products = await self._db.get_all_products()
        active = [p for p in (products or []) if p.get("status") == "active"] or products or []
        if not active:
            return None
        return active[day_index % len(active)]

    async def draft_copy(self, product: dict[str, Any]) -> PinDraft | None:
        """Claude drafts pin title + description + alt_text for one product."""
        name = product.get("name") or product.get("title") or ""
        if not name:
            return None

        # Pick the first image we have
        images = product.get("images") or []
        image_url = ""
        if isinstance(images, list) and images:
            first = images[0]
            image_url = first if isinstance(first, str) else (first.get("src") or first.get("url") or "")
        if not image_url:
            return None

        # Pinterest HTTPS requirement — Pinterest rejects plain http or signed-url images
        if not image_url.startswith("https://"):
            return None

        handle = product.get("handle") or _slugify(name)
        product_url = f"{settings.shopify_storefront_url or 'https://pinakajewellery.com'}/products/{handle}"

        context = {
            "name": name,
            "story": (product.get("story") or "")[:400],
            "materials": product.get("materials", {}),
            "carats": product.get("carats"),
            "metal": (product.get("materials") or {}).get("metal") if isinstance(product.get("materials"), dict) else None,
            "price_range": "$4,500-$5,100",
        }

        if not self._claude:
            # Deterministic fallback
            return PinDraft(
                product_name=name, product_url=product_url, image_url=image_url,
                title=f"{name} — Handcrafted Diamond Tennis Bracelet",
                description=(
                    f"{name}, handcrafted in 14k gold with lab-grown diamonds. "
                    f"Made to order in 15 business days at pinakajewellery.com. "
                    f"From $4,500. A modern choice for milestone gifts, anniversaries, "
                    "and self-purchase. Crafted stone-by-stone by hand in our atelier."
                ),
                alt_text=f"Close-up of {name}, a handcrafted diamond tennis bracelet from Pinaka Jewellery.",
            )

        try:
            response = await self._claude.messages.create(
                model=settings.claude_model,
                max_tokens=700,
                system=PIN_SYSTEM_PROMPT,
                messages=[{"role": "user",
                           "content": f"Product context:\n{json.dumps(context, indent=2)}\n\nReturn JSON."}],
            )
            text = response.content[0].text.strip()
            start, end = text.find("{"), text.rfind("}")
            parsed = json.loads(text[start : end + 1])
            return PinDraft(
                product_name=name,
                product_url=product_url,
                image_url=image_url,
                title=str(parsed.get("title", ""))[:100],
                description=str(parsed.get("description", ""))[:500],
                alt_text=str(parsed.get("alt_text", ""))[:500],
            )
        except Exception:
            logger.exception("Pinterest pin draft Claude call failed")
            return None

    async def create_pin(self, draft: PinDraft) -> PinDraft:
        """POST the draft to Pinterest API v5."""
        if not self.is_configured:
            draft.error = "PINTEREST_ACCESS_TOKEN or PINTEREST_BOARD_ID not set"
            return draft

        payload = {
            "title": draft.title,
            "description": draft.description,
            "alt_text": draft.alt_text,
            "link": draft.product_url,
            "board_id": self._board_id,
            "media_source": {
                "source_type": "image_url",
                "url": draft.image_url,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{PINTEREST_API}/pins",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code not in (200, 201):
                draft.error = f"{resp.status_code}: {resp.text[:280]}"
                return draft
            draft.pin_id = resp.json().get("id")
            return draft
        except Exception as e:
            draft.error = f"network: {e}"
            return draft


def _slugify(value: str) -> str:
    import re
    s = value.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")
