"""Supabase database client for Pinaka agents.

Wraps the Supabase Python client with typed methods. Customer is the primary
entity. Orders, messages, cart_events, and daily_stats link to customers.
"""

import logging
from datetime import date, datetime
from typing import Any

from supabase import create_client

from src.core.settings import settings

logger = logging.getLogger(__name__)


def get_supabase():
    """Create and return a Supabase client instance."""
    return create_client(settings.supabase_url, settings.supabase_key)


class Database:
    """Typed database operations for all Pinaka tables."""

    def __init__(self):
        self._client = get_supabase()

    # ── Customers (primary entity) ──

    def upsert_customer(self, customer_data: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a customer by shopify_customer_id."""
        result = (
            self._client.table("customers")
            .upsert(customer_data, on_conflict="shopify_customer_id")
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_customer_by_shopify_id(self, shopify_customer_id: int) -> dict[str, Any] | None:
        result = (
            self._client.table("customers")
            .select("*")
            .eq("shopify_customer_id", shopify_customer_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_customer_by_email(self, email: str) -> dict[str, Any] | None:
        """Get most recent customer record by email. Not unique (guest checkout)."""
        result = (
            self._client.table("customers")
            .select("*")
            .eq("email", email)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def update_customer_lifecycle(
        self, customer_id: int, stage: str, **extra_fields
    ) -> dict[str, Any]:
        update_data = {"lifecycle_stage": stage, **extra_fields}
        result = (
            self._client.table("customers")
            .update(update_data)
            .eq("id", customer_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_customers_by_lifecycle(self, stage: str) -> list[dict[str, Any]]:
        result = (
            self._client.table("customers")
            .select("*")
            .eq("lifecycle_stage", stage)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data

    # ── Orders ──

    def upsert_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        """Insert or update an order by shopify_order_id (deduplication)."""
        result = (
            self._client.table("orders")
            .upsert(order_data, on_conflict="shopify_order_id")
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_order_by_shopify_id(self, shopify_order_id: int) -> dict[str, Any] | None:
        result = (
            self._client.table("orders")
            .select("*")
            .eq("shopify_order_id", shopify_order_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_orders_by_status(self, status: str) -> list[dict[str, Any]]:
        result = (
            self._client.table("orders")
            .select("*")
            .eq("status", status)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data

    def get_orders_by_customer(self, customer_id: int) -> list[dict[str, Any]]:
        result = (
            self._client.table("orders")
            .select("*")
            .eq("customer_id", customer_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data

    def get_orders_needing_crafting_update(self, days_old: int) -> list[dict[str, Any]]:
        """Get orders that are X days old and haven't received a crafting update."""
        cutoff = (datetime.utcnow() - __import__("datetime").timedelta(days=days_old)).isoformat()
        result = (
            self._client.table("orders")
            .select("*, customers(*)")
            .eq("status", "paid")
            .lte("created_at", cutoff)
            .execute()
        )
        return result.data

    def update_order_status(
        self, shopify_order_id: int, status: str, **extra_fields
    ) -> dict[str, Any]:
        update_data = {"status": status, **extra_fields}
        result = (
            self._client.table("orders")
            .update(update_data)
            .eq("shopify_order_id", shopify_order_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    # ── Messages ──

    def create_message(self, message_data: dict[str, Any]) -> dict[str, Any]:
        result = self._client.table("messages").insert(message_data).execute()
        return result.data[0] if result.data else {}

    def get_pending_messages(self) -> list[dict[str, Any]]:
        result = (
            self._client.table("messages")
            .select("*")
            .eq("status", "pending_review")
            .order("created_at", desc=False)
            .execute()
        )
        return result.data

    def update_message_status(
        self, message_id: int, status: str, **extra_fields
    ) -> dict[str, Any]:
        result = (
            self._client.table("messages")
            .update({"status": status, **extra_fields})
            .eq("id", message_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    # ── Cart Events ──

    def upsert_cart_event(self, cart_data: dict[str, Any]) -> dict[str, Any]:
        result = (
            self._client.table("cart_events")
            .upsert(cart_data, on_conflict="shopify_checkout_token")
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_cart_by_token(self, checkout_token: str) -> dict[str, Any] | None:
        result = (
            self._client.table("cart_events")
            .select("*")
            .eq("shopify_checkout_token", checkout_token)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_cart_by_id(self, cart_id: int) -> dict[str, Any] | None:
        result = (
            self._client.table("cart_events")
            .select("*")
            .eq("id", cart_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_abandoned_carts_pending_recovery(self) -> list[dict[str, Any]]:
        """Get abandoned carts that haven't had a recovery email sent."""
        result = (
            self._client.table("cart_events")
            .select("*")
            .eq("event_type", "abandoned")
            .is_("recovery_email_status", "null")
            .order("created_at", desc=False)
            .execute()
        )
        return result.data

    def cancel_cart_recovery(self, checkout_token: str) -> None:
        """Cancel pending recovery email when order completes."""
        self._client.table("cart_events").update(
            {"recovery_email_status": "cancelled", "event_type": "recovered"}
        ).eq("shopify_checkout_token", checkout_token).in_(
            "recovery_email_status", ["pending", None]
        ).execute()

    # ── Daily Stats ──

    def upsert_daily_stats(self, stats_data: dict[str, Any]) -> dict[str, Any]:
        result = (
            self._client.table("daily_stats")
            .upsert(stats_data, on_conflict="date")
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_stats_range(
        self, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        result = (
            self._client.table("daily_stats")
            .select("*")
            .gte("date", start_date.isoformat())
            .lte("date", end_date.isoformat())
            .order("date", desc=False)
            .execute()
        )
        return result.data

    # ── Fraud Detection ──

    def count_orders_from_email_24h(self, buyer_email: str) -> int:
        """Count recent orders from the same buyer for velocity fraud detection."""
        result = (
            self._client.table("orders")
            .select("id", count="exact")
            .eq("buyer_email", buyer_email)
            .gte("created_at", (datetime.utcnow().replace(hour=0, minute=0, second=0)).isoformat())
            .execute()
        )
        return result.count or 0

    # ── Aggregations ──

    def get_total_revenue(self, start_date: date, end_date: date) -> float:
        result = (
            self._client.table("orders")
            .select("total")
            .gte("created_at", start_date.isoformat())
            .lte("created_at", end_date.isoformat())
            .execute()
        )
        return sum(float(r["total"]) for r in result.data) if result.data else 0.0

    def get_customer_count(self) -> int:
        result = (
            self._client.table("customers")
            .select("id", count="exact")
            .execute()
        )
        return result.count or 0

    def get_repeat_customer_count(self) -> int:
        result = (
            self._client.table("customers")
            .select("id", count="exact")
            .in_("lifecycle_stage", ["repeat", "advocate"])
            .execute()
        )
        return result.count or 0
