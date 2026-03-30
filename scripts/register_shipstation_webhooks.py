"""Register ShipStation webhooks for tracking notifications.

Usage:
    APP_BASE_URL=https://your-app.railway.app python scripts/register_shipstation_webhooks.py

Registers for SHIP_NOTIFY events so we get real-time tracking updates.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.settings import settings
from src.core.rate_limiter import RateLimitedClient


async def main() -> None:
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if not base_url:
        print("ERROR: Set APP_BASE_URL env var (e.g. https://your-app.railway.app)")
        sys.exit(1)

    if not settings.shipstation_api_key:
        print("ERROR: SHIPSTATION_API_KEY not set")
        sys.exit(1)

    secret = settings.shipstation_webhook_secret or settings.cron_secret
    webhook_url = f"{base_url}/webhook/shipstation?secret={secret}"

    client = RateLimitedClient(
        base_url=settings.shipstation_base_url,
        qps=settings.shipstation_qps,
        headers={"Content-Type": "application/json"},
        auth=(settings.shipstation_api_key, settings.shipstation_api_secret),
    )

    try:
        # List existing webhooks
        resp = await client.get("/webhooks")
        if resp.status_code == 200:
            existing = resp.json().get("webhooks", [])
            print(f"Found {len(existing)} existing ShipStation webhook(s)")
            for wh in existing:
                print(f"  - {wh.get('HookType', 'unknown')}: {wh.get('WebHookURI', '')}")
        else:
            print(f"Warning: Could not list webhooks: {resp.status_code}")
            existing = []

        # Register SHIP_NOTIFY if not already present
        ship_notify_exists = any(
            wh.get("HookType") == "SHIP_NOTIFY" for wh in existing
        )

        if ship_notify_exists:
            print("\nSHIP_NOTIFY webhook already registered, skipping")
        else:
            resp = await client.post("/webhooks/subscribe", json={
                "target_url": webhook_url,
                "event": "SHIP_NOTIFY",
                "store_id": None,
                "friendly_name": "Pinaka Tracking Updates",
            })
            if resp.status_code in (200, 201):
                result = resp.json()
                print(f"\nRegistered SHIP_NOTIFY webhook (ID: {result.get('id')})")
                print(f"  URL: {webhook_url}")
            else:
                print(f"\nFailed to register SHIP_NOTIFY: {resp.status_code} {resp.text}")

        print("\nDone. Verify at ShipStation > Settings > Integrations > Webhook")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
