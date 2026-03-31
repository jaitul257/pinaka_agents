"""Shopify Admin API client with rate limiting.

Uses the shared RateLimitedClient for all HTTP calls. Handles:
- Admin API authentication via X-Shopify-Access-Token header
- Typed methods for orders, customers, products, and webhooks
- Rate limiting at Shopify's 2 req/sec for Basic plan
"""

import logging
from typing import Any

from src.core.rate_limiter import RateLimitedClient
from src.core.settings import settings

logger = logging.getLogger(__name__)


class ShopifyClient:
    """Typed Shopify Admin API client with rate limiting."""

    def __init__(self):
        self._client = RateLimitedClient(
            base_url=settings.shopify_admin_url,
            qps=settings.shopify_qps,
            headers={
                "X-Shopify-Access-Token": settings.shopify_access_token,
                "Content-Type": "application/json",
            },
        )

    # ── Orders ──

    async def get_orders(
        self,
        status: str = "any",
        limit: int = 50,
        since_id: int | None = None,
        created_at_min: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch orders from Shopify."""
        params: dict[str, Any] = {"status": status, "limit": limit}
        if since_id:
            params["since_id"] = since_id
        if created_at_min:
            params["created_at_min"] = created_at_min

        response = await self._client.request("GET", "/orders.json", params=params)
        response.raise_for_status()
        return response.json().get("orders", [])

    async def get_order(self, order_id: int) -> dict[str, Any]:
        response = await self._client.request("GET", f"/orders/{order_id}.json")
        response.raise_for_status()
        return response.json().get("order", {})

    # ── Customers ──

    async def get_customer(self, customer_id: int) -> dict[str, Any]:
        response = await self._client.request("GET", f"/customers/{customer_id}.json")
        response.raise_for_status()
        return response.json().get("customer", {})

    async def search_customers(self, query: str) -> list[dict[str, Any]]:
        """Search customers by email, name, etc."""
        response = await self._client.request(
            "GET", "/customers/search.json", params={"query": query}
        )
        response.raise_for_status()
        return response.json().get("customers", [])

    # ── Products ──

    async def get_products(self, limit: int = 50) -> list[dict[str, Any]]:
        response = await self._client.request(
            "GET", "/products.json", params={"limit": limit}
        )
        response.raise_for_status()
        return response.json().get("products", [])

    async def get_product(self, product_id: int) -> dict[str, Any]:
        response = await self._client.request("GET", f"/products/{product_id}.json")
        response.raise_for_status()
        return response.json().get("product", {})

    # ── Abandoned Checkouts ──

    async def get_abandoned_checkouts(
        self,
        updated_at_min: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch abandoned checkouts for reconciliation."""
        params: dict[str, Any] = {"limit": limit}
        if updated_at_min:
            params["updated_at_min"] = updated_at_min

        response = await self._client.request(
            "GET", "/checkouts.json", params=params
        )
        response.raise_for_status()
        return response.json().get("checkouts", [])

    # ── Webhooks ──

    async def get_webhooks(self) -> list[dict[str, Any]]:
        """List all registered webhook subscriptions."""
        response = await self._client.request("GET", "/webhooks.json")
        response.raise_for_status()
        return response.json().get("webhooks", [])

    async def create_webhook(self, topic: str, address: str) -> dict[str, Any]:
        """Register a new webhook subscription."""
        response = await self._client.request(
            "POST",
            "/webhooks.json",
            json={"webhook": {"topic": topic, "address": address, "format": "json"}},
        )
        response.raise_for_status()
        return response.json().get("webhook", {})

    # ── GraphQL ──

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GraphQL Admin API query."""
        response = await self._client.request(
            "POST",
            "/graphql.json",
            json={"query": query, "variables": variables or {}},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            logger.error("GraphQL errors: %s", data["errors"])
        return data.get("data", {})

    # ── Products (create via GraphQL) ──

    async def create_product(
        self,
        title: str,
        body_html: str,
        tags: list[str],
        product_type: str = "",
        vendor: str = "Pinaka Jewellery",
    ) -> dict[str, Any]:
        """Create a product in Shopify as draft via GraphQL."""
        mutation = """
        mutation productCreate($product: ProductCreateInput!) {
            productCreate(product: $product) {
                product {
                    id
                    title
                    handle
                    status
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "product": {
                "title": title,
                "descriptionHtml": body_html,
                "tags": tags,
                "productType": product_type,
                "vendor": vendor,
                "status": "DRAFT",
            }
        }

        data = await self._graphql(mutation, variables)
        result = data.get("productCreate") or {}

        user_errors = result.get("userErrors") or []
        if user_errors:
            logger.error("Product create errors: %s", user_errors)
            return {"error": user_errors}

        product = result.get("product") or {}
        if not product:
            logger.error("No product returned from productCreate")
            return {}

        # Extract numeric ID from GID (gid://shopify/Product/12345 -> 12345)
        gid = product.get("id", "")
        numeric_id = gid.split("/")[-1] if gid else ""
        return {"id": numeric_id, "title": product.get("title", ""), "handle": product.get("handle", "")}

    # ── Fulfillment ──

    async def get_fulfillment_orders(self, order_id: int) -> list[dict[str, Any]]:
        """Get fulfillment orders for a Shopify order (required by Fulfillment Orders API)."""
        response = await self._client.request(
            "GET", f"/orders/{order_id}/fulfillment_orders.json"
        )
        response.raise_for_status()
        return response.json().get("fulfillment_orders", [])

    async def create_fulfillment(
        self, order_id: int, tracking_number: str, tracking_company: str
    ) -> dict[str, Any]:
        """Create a fulfillment via the Fulfillment Orders API.

        Uses notify_customer=False because we send branded emails via SendGrid.
        """
        # Get fulfillment order IDs
        fulfillment_orders = await self.get_fulfillment_orders(order_id)
        if not fulfillment_orders:
            logger.warning("No fulfillment orders found for order %s", order_id)
            return {}

        # Use the first open fulfillment order
        fo = next(
            (fo for fo in fulfillment_orders if fo.get("status") == "open"),
            fulfillment_orders[0],
        )
        fo_id = fo["id"]

        line_items = [
            {"id": li["id"], "quantity": li["quantity"]}
            for li in fo.get("line_items", [])
        ]

        response = await self._client.request(
            "POST",
            "/fulfillments.json",
            json={
                "fulfillment": {
                    "line_items_by_fulfillment_order": [
                        {"fulfillment_order_id": fo_id, "fulfillment_order_line_items": line_items}
                    ],
                    "tracking_info": {
                        "number": tracking_number,
                        "company": tracking_company,
                    },
                    "notify_customer": False,
                }
            },
        )
        response.raise_for_status()
        return response.json().get("fulfillment", {})

    # ── Cleanup ──

    async def close(self) -> None:
        await self._client.close()
