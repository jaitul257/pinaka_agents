"""Weekly SEO journal post writer (Phase 9.3).

Long-tail content for organic search — the moat at our AOV. At $4,500-$5,100
per bracelet, we cannot out-bid Blue Nile / Brilliant Earth / James Allen on
head terms. We win on tail: specific milestones, comparison questions,
anniversary gift guides.

Each weekly run:
  1. Picks the next-due keyword from seo_topics table (oldest last_used_at)
  2. Claude drafts 900-1400 word post + title + meta description + slug
  3. If SHOPIFY_BLOG_ID is set + write_content scope is granted:
       POSTs to /admin/api/2025-01/blogs/{id}/articles.json as draft
       Returns admin URL for review
     Otherwise:
       Returns the full body markdown for Slack copy-paste
  4. Marks keyword last_used_at = now(), times_used += 1
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import anthropic
import httpx

from src.core.database import AsyncDatabase
from src.core.settings import settings

logger = logging.getLogger(__name__)


# Curated long-tail keywords. Loaded into seo_topics table on first run if
# the table is empty. Editing this list adds new topics on next sync; removing
# doesn't auto-delete existing DB rows (set active=false manually).
SEO_KEYWORDS: list[tuple[str, str]] = [
    # (keyword, category)
    # Anniversary — highest-intent for tennis bracelets
    ("diamond tennis bracelet for 10 year anniversary", "anniversary"),
    ("tennis bracelet for 5 year anniversary gift", "anniversary"),
    ("anniversary gift ideas under $5000 fine jewelry", "anniversary"),
    ("milestone anniversary bracelet ideas", "anniversary"),
    ("25th anniversary tennis bracelet", "anniversary"),
    # Comparison / education — trust-building
    ("handcrafted vs mass produced tennis bracelet", "comparison"),
    ("lab grown vs natural diamond tennis bracelet", "comparison"),
    ("why are tennis bracelets so expensive", "education"),
    ("how to tell if a tennis bracelet is real", "education"),
    ("4Cs of diamonds explained for bracelets", "education"),
    ("tennis bracelet clasp types explained", "education"),
    ("yellow gold vs white gold tennis bracelet", "comparison"),
    # Occasion
    ("self purchase jewelry for women 30s", "occasion"),
    ("engagement gift for her beyond a ring", "occasion"),
    ("push present ideas fine jewelry", "occasion"),
    ("graduation gift fine jewelry", "occasion"),
    # Style / fit
    ("tennis bracelet how tight should it fit", "education"),
    ("can you wear a tennis bracelet every day", "education"),
    ("tennis bracelet care and cleaning guide", "education"),
    ("how to layer tennis bracelets with watch", "education"),
    # Pricing / value
    ("is a $5000 tennis bracelet worth it", "education"),
    ("why made to order jewelry is worth the wait", "education"),
    # Specific products / cuts
    ("round brilliant vs emerald cut tennis bracelet", "comparison"),
    ("3 carat tennis bracelet what to expect", "education"),
    ("bezel set vs prong set tennis bracelet", "comparison"),
]


@dataclass
class SEOPostDraft:
    """A generated post ready to post to Shopify or Slack."""
    keyword: str
    category: str
    title: str
    meta_description: str
    slug: str
    body_html: str
    body_markdown: str
    tags: list[str]
    word_count: int
    # Populated after Shopify publish (if configured)
    shopify_article_id: int | None = None
    shopify_admin_url: str | None = None
    publish_error: str | None = None


SEO_SYSTEM_PROMPT = """You write long-form journal posts for Pinaka Jewellery — a premium \
handcrafted diamond tennis bracelet brand ($4,500-$5,100 AOV). Your audience is considering \
a major fine-jewelry purchase and searching for specific long-tail information on Google.

Your job: write an SEO-optimized journal post targeting the exact keyword provided.

Constraints:
- 900-1,400 words. Not less, not more.
- Natural keyword density — include the exact keyword 3-5 times, naturally.
- H1 is the post title (answers the exact query).
- 3-5 H2 sections.
- Bullet lists where they clarify (not for padding).
- Short paragraphs (2-4 sentences each). Scan-friendly.
- Warm, expert, first-person-plural voice ("we believe", "our setters").
- Concrete details: actual gold weights, actual days, actual prices ($4,500-$5,100).
- Reference Pinaka's specific craft (15 business days, 14k/18k gold, lab-grown or mined diamond options) by name, but never more than 2 times — this isn't a product pamphlet.
- NO LLM tells: no "In conclusion", no "let's dive in", no "unlock", no "journey".
- NO motivational filler. No "you deserve". No "invest in yourself" platitudes.
- Close with one specific, non-pushy CTA (browse, book a call, or read another post).

Output strict JSON:
{
  "title": "...",            // H1, max 65 chars for SERP
  "meta_description": "...", // 150-160 chars for SERP
  "slug": "...",             // URL-safe, 4-8 words, hyphenated, lowercase
  "tags": ["tag1", "tag2"],  // 3-5 short topical tags
  "body_markdown": "..."     // Full post body in markdown (starts with H1)
}"""


class SEOWriter:
    """Generate weekly SEO journal posts and (optionally) publish drafts to Shopify."""

    def __init__(self):
        self._db = AsyncDatabase()
        self._claude = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key else None
        )

    # ── Topic rotation ──

    async def ensure_topics_seeded(self) -> int:
        """Insert any new SEO_KEYWORDS that aren't yet in seo_topics. Idempotent."""
        client = self._db._sync._client
        import asyncio
        existing = await asyncio.to_thread(
            lambda: client.table("seo_topics").select("keyword").execute()
        )
        existing_keywords = {row["keyword"] for row in (existing.data or [])}
        new_rows = [
            {"keyword": kw, "category": cat}
            for kw, cat in SEO_KEYWORDS
            if kw not in existing_keywords
        ]
        if not new_rows:
            return 0
        await asyncio.to_thread(
            lambda: client.table("seo_topics").insert(new_rows).execute()
        )
        logger.info("Seeded %d new SEO topics", len(new_rows))
        return len(new_rows)

    async def next_topic(self) -> dict[str, Any] | None:
        """Pick the next keyword: active, oldest last_used_at (NULLS FIRST), then least-used."""
        client = self._db._sync._client
        import asyncio
        result = await asyncio.to_thread(
            lambda: (
                client.table("seo_topics")
                .select("*")
                .eq("active", True)
                .order("last_used_at", desc=False, nullsfirst=True)
                .order("times_used", desc=False)
                .limit(1)
                .execute()
            )
        )
        return result.data[0] if result.data else None

    async def mark_used(
        self, topic_id: int, *, article_id: int | None = None, admin_url: str | None = None,
    ) -> None:
        client = self._db._sync._client
        import asyncio
        updates = {
            "last_used_at": datetime.now(timezone.utc).isoformat(),
            "times_used": None,  # we'll bump below via RPC-free pattern
        }
        # Supabase Python client doesn't expose atomic increment — read-modify-write
        existing = await asyncio.to_thread(
            lambda: client.table("seo_topics").select("times_used").eq("id", topic_id).execute()
        )
        current = int((existing.data[0].get("times_used") if existing.data else 0) or 0)
        updates["times_used"] = current + 1
        if article_id:
            updates["last_shopify_article_id"] = article_id
        if admin_url:
            updates["last_draft_url"] = admin_url
        await asyncio.to_thread(
            lambda: client.table("seo_topics").update(updates).eq("id", topic_id).execute()
        )

    # ── Drafting ──

    async def draft(self, keyword: str, category: str) -> SEOPostDraft:
        if not self._claude:
            raise RuntimeError("No Anthropic API key configured for SEO writer")

        user_prompt = (
            f"Target keyword: {keyword}\n"
            f"Category: {category}\n\n"
            "Write the journal post now. Return JSON only."
        )
        response = await self._claude.messages.create(
            model=settings.claude_model,
            max_tokens=5000,
            system=SEO_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx < 0 or end_idx <= start_idx:
            raise RuntimeError("SEO draft: no JSON in Claude response")
        parsed = json.loads(text[start_idx : end_idx + 1])

        title = str(parsed.get("title") or "").strip()[:110]
        meta = str(parsed.get("meta_description") or "").strip()[:165]
        slug = _slugify(str(parsed.get("slug") or title))
        tags = [str(t).strip() for t in (parsed.get("tags") or []) if t][:6]
        body_md = str(parsed.get("body_markdown") or "").strip()
        body_html = _markdown_to_html(body_md)
        word_count = len(re.findall(r"\w+", body_md))

        return SEOPostDraft(
            keyword=keyword, category=category, title=title,
            meta_description=meta, slug=slug, tags=tags,
            body_html=body_html, body_markdown=body_md, word_count=word_count,
        )

    # ── Shopify publish (optional) ──

    @property
    def shopify_publish_enabled(self) -> bool:
        return bool(
            settings.shopify_shop_domain
            and settings.shopify_access_token
            and settings.shopify_blog_id
        )

    async def publish_draft(self, draft: SEOPostDraft) -> SEOPostDraft:
        """Create a DRAFT article in Shopify's blog.

        Requires `write_content` scope on the access token. If the scope is
        missing or settings aren't configured, populates `publish_error` and
        returns the draft unchanged — caller should fall back to Slack paste.
        """
        if not self.shopify_publish_enabled:
            draft.publish_error = "SHOPIFY_BLOG_ID or SHOPIFY_ACCESS_TOKEN not set"
            return draft

        url = (
            f"https://{settings.shopify_shop_domain}"
            f"/admin/api/{settings.shopify_api_version}"
            f"/blogs/{settings.shopify_blog_id}/articles.json"
        )
        payload = {
            "article": {
                "title": draft.title,
                "body_html": draft.body_html,
                "tags": ", ".join(draft.tags),
                "summary_html": f"<p>{draft.meta_description}</p>",
                "handle": draft.slug,
                "published": False,  # DRAFT — founder reviews and publishes
                "metafields": [
                    {"namespace": "global", "key": "description_tag",
                     "type": "single_line_text_field", "value": draft.meta_description},
                    {"namespace": "global", "key": "title_tag",
                     "type": "single_line_text_field", "value": draft.title},
                ],
            },
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "X-Shopify-Access-Token": settings.shopify_access_token,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code == 403:
                draft.publish_error = "403 — token missing write_content scope. Re-install app."
                return draft
            if resp.status_code not in (200, 201):
                draft.publish_error = f"{resp.status_code}: {resp.text[:280]}"
                return draft
            article = resp.json().get("article", {})
            draft.shopify_article_id = article.get("id")
            # Admin URL for the draft (founder clicks to review + publish)
            draft.shopify_admin_url = (
                f"https://admin.shopify.com/store/{settings.shopify_shop_domain.split('.')[0]}"
                f"/blogs/{settings.shopify_blog_id}/articles/{draft.shopify_article_id}"
            )
            return draft
        except Exception as e:
            draft.publish_error = f"network: {e}"
            return draft


def _slugify(value: str) -> str:
    """URL-safe slug: lowercase, spaces → hyphens, strip punctuation."""
    s = value.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")[:80] or "untitled"


def _markdown_to_html(md: str) -> str:
    """Minimal markdown → HTML for Shopify's blog body.

    Handles headings, paragraphs, lists, bold/italic. No external dep.
    """
    lines = md.splitlines()
    html_parts: list[str] = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        if not line:
            close_list()
            continue

        # Headings
        h_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if h_match:
            close_list()
            level = len(h_match.group(1))
            # Shopify blog body already has H1 at the article title — downshift H1→H2 etc.
            adjusted = min(level + 1, 6)
            html_parts.append(f"<h{adjusted}>{_inline_md(h_match.group(2))}</h{adjusted}>")
            continue

        # Bullet list
        if line.startswith(("- ", "* ")):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_md(line[2:])}</li>")
            continue

        # Paragraph
        close_list()
        html_parts.append(f"<p>{_inline_md(line)}</p>")

    close_list()
    return "\n".join(html_parts)


def _inline_md(text: str) -> str:
    """Bold, italic, inline code for inline markdown."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text
