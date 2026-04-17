"""One-time script to register Shopify webhooks.

Usage:
    APP_BASE_URL=https://your-app.fly.dev python scripts/register_webhooks.py

Registers webhook subscriptions for:
- orders/create  → /webhook/shopify/orders
- customers/create → /webhook/shopify/customers
- checkouts/create → /webhook/shopify/checkouts

Idempotent: skips topics that already have a subscription.
"""

import asyncio
import os
import sys

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.shopify_client import ShopifyClient

WEBHOOK_TOPICS = {
    "orders/create": "/webhook/shopify/orders",
    "customers/create": "/webhook/shopify/customers",
    "customers/update": "/webhook/shopify/customers",
    "checkouts/create": "/webhook/shopify/checkouts",
    "refunds/create": "/webhook/shopify/refund",
    # Phase 11: product mirroring so Supabase stays in real-time sync with
    # Shopify admin changes (new product creation, edits, deletions)
    "products/create": "/webhook/shopify/products",
    "products/update": "/webhook/shopify/products",
    "products/delete": "/webhook/shopify/products-delete",
}


async def main() -> None:
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not base_url:
        print("ERROR: Set APP_BASE_URL env var (e.g. https://your-app.fly.dev)")
        sys.exit(1)

    client = ShopifyClient()

    try:
        existing = await client.get_webhooks()
        existing_topics = {wh["topic"] for wh in existing}
        print(f"Found {len(existing)} existing webhook(s): {existing_topics or 'none'}")

        for topic, path in WEBHOOK_TOPICS.items():
            if topic in existing_topics:
                print(f"  SKIP {topic} (already registered)")
                continue

            address = f"{base_url}{path}"
            webhook = await client.create_webhook(topic=topic, address=address)
            print(f"  OK   {topic} → {address} (id: {webhook.get('id')})")

        print("\nDone. Verify at Shopify Admin → Settings → Notifications → Webhooks")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
