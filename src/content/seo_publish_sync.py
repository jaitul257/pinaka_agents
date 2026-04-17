"""Shopify blog publish status reverse-sync.

When we auto-publish an SEO draft via the weekly cron, we store the
Shopify article_id in seo_topics.last_shopify_article_id and the row
starts with last_published_at = NULL (it's a draft).

Later, the founder opens Shopify admin, reviews the draft, and clicks
Publish. Without this sync, we don't know it went live — the SEO topic
shows as "drafted but not confirmed live" forever.

Weekly cron fetches each article from Shopify, reads its `published_at`
timestamp, and mirrors back to `seo_topics.last_published_at`. When a
draft transitions to published, post a Slack celebration (positive signal —
SEO content is working its way through the funnel).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from src.core.database import AsyncDatabase
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


async def reconcile_seo_publish_status(
    db: AsyncDatabase | None = None,
    notify_newly_published: bool = True,
) -> dict[str, int]:
    """Fetch each Shopify article we've tracked, mirror its publish state
    back into seo_topics. Returns counts for logging.
    """
    db = db or AsyncDatabase()

    if not (
        settings.shopify_shop_domain
        and settings.shopify_access_token
        and settings.shopify_blog_id
    ):
        return {
            "skip_reason": "shopify_blog_not_configured",
            "checked": 0, "newly_published": 0, "missing": 0, "errors": 0,
        }

    # Pull every seo_topics row that has a Shopify article_id linked
    client = db._sync._client
    import asyncio
    resp = await asyncio.to_thread(
        lambda: (
            client.table("seo_topics")
            .select("id,keyword,last_shopify_article_id,last_published_at")
            .neq("last_shopify_article_id", None)
            .execute()
        )
    )
    rows = resp.data or []
    if not rows:
        return {"checked": 0, "newly_published": 0, "missing": 0, "errors": 0}

    checked = 0
    newly_published = 0
    missing = 0
    errors = 0
    publish_notifications: list[tuple[str, str]] = []  # (keyword, article_url)

    async with httpx.AsyncClient(timeout=15) as http:
        for row in rows:
            checked += 1
            article_id = row.get("last_shopify_article_id")
            if not article_id:
                continue

            url = (
                f"https://{settings.shopify_shop_domain}"
                f"/admin/api/{settings.shopify_api_version}"
                f"/articles/{article_id}.json"
            )
            try:
                r = await http.get(
                    url,
                    headers={"X-Shopify-Access-Token": settings.shopify_access_token},
                )
            except Exception:
                logger.exception("seo_sync: network error fetching article %s", article_id)
                errors += 1
                continue

            if r.status_code == 404:
                # Article deleted in Shopify — clear our pointer
                missing += 1
                try:
                    await asyncio.to_thread(
                        lambda rid=row["id"]: (
                            client.table("seo_topics")
                            .update({"last_shopify_article_id": None, "last_published_at": None})
                            .eq("id", rid)
                            .execute()
                        )
                    )
                except Exception:
                    logger.exception("seo_sync: failed to clear pointer for seo_topic %s", row["id"])
                    errors += 1
                continue

            if r.status_code != 200:
                logger.warning("seo_sync: article %s returned %d", article_id, r.status_code)
                errors += 1
                continue

            article = r.json().get("article", {}) or {}
            published_at = article.get("published_at")  # None if draft, ISO if live

            # Detect newly-published: was None before, now has a timestamp
            was_published = bool(row.get("last_published_at"))
            is_published_now = bool(published_at)

            if is_published_now and not was_published:
                newly_published += 1
                handle = article.get("handle", "")
                article_url = (
                    f"https://{settings.shopify_storefront_url.replace('https://','') or 'pinakajewellery.com'}"
                    f"/blogs/news/{handle}"
                )
                publish_notifications.append((row.get("keyword", ""), article_url))

            # Update if anything changed
            if is_published_now != was_published:
                try:
                    await asyncio.to_thread(
                        lambda rid=row["id"], pa=published_at: (
                            client.table("seo_topics")
                            .update({"last_published_at": pa})
                            .eq("id", rid)
                            .execute()
                        )
                    )
                except Exception:
                    logger.exception("seo_sync: failed to update publish timestamp for %s", row["id"])
                    errors += 1

    # Slack celebration for newly-published posts (positive signal, don't drop)
    if notify_newly_published and publish_notifications:
        try:
            slack = SlackNotifier()
            blocks: list[dict[str, Any]] = [
                {"type": "header", "text": {"type": "plain_text",
                                            "text": f":newspaper: {len(publish_notifications)} SEO post(s) went live"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": "Drafts you published since last check — indexed by Google soon."}},
            ]
            for kw, url in publish_notifications[:10]:
                blocks.append({"type": "section", "text": {"type": "mrkdwn",
                    "text": f"• *{kw}* → <{url}|View on storefront>"}})
            await slack.send_blocks(blocks, text=f"{len(publish_notifications)} SEO posts published")
        except Exception:
            logger.exception("seo_sync: Slack notification failed (non-fatal)")

    return {
        "checked": checked,
        "newly_published": newly_published,
        "missing": missing,
        "errors": errors,
    }
