"""Supabase database client for Pinaka agents.

Wraps the Supabase Python client with typed methods. Customer is the primary
entity. Orders, messages, cart_events, and daily_stats link to customers.

AsyncDatabase wraps all sync methods via asyncio.to_thread() for non-blocking
use in FastAPI async handlers.
"""

import asyncio
import functools
import logging
from datetime import date, datetime, timedelta
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

    def update_order_tracking(
        self,
        shopify_order_id: int,
        tracking_number: str,
        carrier: str,
        status: str,
        tracking_url: str = "",
        **extra_fields,
    ) -> dict[str, Any]:
        """Update order with tracking info from ShipStation webhook."""
        update_data = {
            "tracking_number": tracking_number,
            "shipping_carrier": carrier,
            "status": status,
            "tracking_url": tracking_url,
            **extra_fields,
        }
        result = (
            self._client.table("orders")
            .update(update_data)
            .eq("shopify_order_id", shopify_order_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_order_by_shipstation_id(self, shipstation_order_id: int) -> dict[str, Any] | None:
        result = (
            self._client.table("orders")
            .select("*")
            .eq("shipstation_order_id", shipstation_order_id)
            .execute()
        )
        return result.data[0] if result.data else None

    # ── Chargeback Evidence ──

    def get_chargeback_evidence(self, shopify_order_id: int) -> dict[str, Any] | None:
        """Collect all evidence fields for a chargeback dispute response."""
        result = (
            self._client.table("orders")
            .select("*, customers(*)")
            .eq("shopify_order_id", shopify_order_id)
            .execute()
        )
        if not result.data:
            return None

        order = result.data[0]
        customer = order.get("customers") or {}

        return {
            "order_number": order.get("shopify_order_id"),
            "order_confirmed_at": order.get("created_at"),
            "order_total": order.get("total"),
            "customer_email": order.get("buyer_email"),
            "customer_name": order.get("buyer_name"),
            "tracking_number": order.get("tracking_number"),
            "shipping_carrier": order.get("shipping_carrier"),
            "tracking_url": order.get("tracking_url"),
            "shipped_at": order.get("shipped_at"),
            "delivered_at": order.get("delivered_at"),
            "evidence_collected_at": order.get("evidence_collected_at"),
            "customer_lifecycle": customer.get("lifecycle_stage"),
            "customer_order_count": customer.get("order_count"),
        }

    def get_shipped_orders_pending_delivery(self, shipped_before_days: int = 7) -> list[dict[str, Any]]:
        """Get orders that shipped but haven't been marked delivered yet."""
        cutoff = (datetime.utcnow() - __import__("datetime").timedelta(days=shipped_before_days)).isoformat()
        result = (
            self._client.table("orders")
            .select("*")
            .eq("status", "shipped")
            .lte("shipped_at", cutoff)
            .is_("delivered_at", "null")
            .execute()
        )
        return result.data or []

    def mark_evidence_collected(self, shopify_order_id: int) -> None:
        """Mark that chargeback evidence has been collected for an order."""
        self._client.table("orders").update(
            {"evidence_collected_at": datetime.utcnow().isoformat()}
        ).eq("shopify_order_id", shopify_order_id).execute()

    # ── Refunds ──

    def get_refund_by_shopify_id(self, shopify_refund_id: int) -> dict[str, Any] | None:
        """Check if a refund has already been processed (idempotency)."""
        result = (
            self._client.table("refunds")
            .select("*")
            .eq("shopify_refund_id", shopify_refund_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def create_refund(self, refund_data: dict[str, Any]) -> dict[str, Any]:
        """Insert a refund event and update the parent order's refund_amount."""
        result = self._client.table("refunds").insert(refund_data).execute()
        refund = result.data[0] if result.data else {}

        if refund:
            # Accumulate refund_amount on the order
            order_id = refund_data["order_id"]
            refunds_result = (
                self._client.table("refunds")
                .select("amount")
                .eq("order_id", order_id)
                .execute()
            )
            total_refunded = sum(float(r["amount"]) for r in refunds_result.data)

            # Get order total to determine if fully refunded
            order_result = (
                self._client.table("orders")
                .select("total, shopify_order_id")
                .eq("id", order_id)
                .execute()
            )
            order = order_result.data[0] if order_result.data else {}
            order_total = float(order.get("total", 0))

            update_data: dict[str, Any] = {
                "refund_amount": min(total_refunded, order_total),
                "refunded_at": refund_data.get("created_at", datetime.utcnow().isoformat()),
            }
            # Set status to "refunded" only if fully refunded
            if total_refunded >= order_total:
                update_data["status"] = "refunded"

            self._client.table("orders").update(update_data).eq("id", order_id).execute()

        return refund

    # ── Reorder Reminders ──

    def get_customers_for_reorder(
        self, days_since_purchase: int, cooldown_days: int = 180
    ) -> list[dict[str, Any]]:
        """Get customers eligible for reorder reminders.

        Filters: accepts_marketing=True, has an order near the target day window,
        and hasn't received a reorder email within cooldown_days.
        """
        from datetime import timedelta

        # Target window: orders placed days_since_purchase ago (+/- 7 days)
        target_date = datetime.utcnow() - timedelta(days=days_since_purchase)
        window_start = (target_date - timedelta(days=7)).isoformat()
        window_end = (target_date + timedelta(days=7)).isoformat()

        # Get customers who accept marketing
        customers_result = (
            self._client.table("customers")
            .select("*, orders(*)")
            .eq("accepts_marketing", True)
            .execute()
        )

        candidates = []
        cooldown_cutoff = (datetime.utcnow() - timedelta(days=cooldown_days)).isoformat()

        for customer in customers_result.data or []:
            # Skip if recently emailed
            last_reorder = customer.get("last_reorder_email_at")
            if last_reorder and last_reorder > cooldown_cutoff:
                continue

            # Check if any order falls in the target window
            orders = customer.get("orders", [])
            matching_orders = [
                o for o in orders
                if o.get("created_at", "") >= window_start
                and o.get("created_at", "") <= window_end
            ]

            if matching_orders:
                # Use the most recent matching order
                latest = max(matching_orders, key=lambda o: o.get("created_at", ""))
                candidates.append({
                    "customer": customer,
                    "last_order": latest,
                })

        return candidates

    def update_customer_reorder_sent(self, customer_id: int) -> None:
        """Mark that a reorder reminder was sent to this customer."""
        self._client.table("customers").update(
            {"last_reorder_email_at": datetime.utcnow().isoformat()}
        ).eq("id", customer_id).execute()

    # ── Voice Examples ──

    def create_voice_example(self, data: dict[str, Any]) -> dict[str, Any]:
        """Save an approved draft as a voice learning example."""
        result = self._client.table("voice_examples").insert(data).execute()
        return result.data[0] if result.data else {}

    def get_voice_examples(
        self, category: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get recent voice examples for a category (newest first)."""
        result = (
            self._client.table("voice_examples")
            .select("*")
            .eq("category", category)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    def get_voice_example_count(self, category: str) -> int:
        """Count voice examples for a category."""
        result = (
            self._client.table("voice_examples")
            .select("id", count="exact")
            .eq("category", category)
            .execute()
        )
        return result.count or 0

    def prune_voice_examples(self, category: str, max_per_category: int = 100) -> int:
        """Delete oldest examples if category exceeds max. Returns count deleted."""
        count = self.get_voice_example_count(category)
        if count <= max_per_category:
            return 0

        # Get IDs to keep (newest max_per_category)
        keep_result = (
            self._client.table("voice_examples")
            .select("id")
            .eq("category", category)
            .order("created_at", desc=True)
            .limit(max_per_category)
            .execute()
        )
        keep_ids = {r["id"] for r in keep_result.data or []}

        # Get all IDs for category
        all_result = (
            self._client.table("voice_examples")
            .select("id")
            .eq("category", category)
            .execute()
        )
        all_ids = {r["id"] for r in all_result.data or []}

        # Delete the ones not in keep set
        delete_ids = all_ids - keep_ids
        for did in delete_ids:
            self._client.table("voice_examples").delete().eq("id", did).execute()

        return len(delete_ids)

    def get_voice_stats(self) -> dict[str, Any]:
        """Get voice learning stats: examples per category, edit rates."""
        result = (
            self._client.table("voice_examples")
            .select("category, was_edited")
            .execute()
        )
        categories: dict[str, dict[str, int]] = {}
        for row in result.data or []:
            cat = row["category"]
            if cat not in categories:
                categories[cat] = {"total": 0, "edited": 0}
            categories[cat]["total"] += 1
            if row.get("was_edited"):
                categories[cat]["edited"] += 1

        total = sum(c["total"] for c in categories.values())
        total_edited = sum(c["edited"] for c in categories.values())

        return {
            "total_examples": total,
            "total_edited": total_edited,
            "edit_rate": round(total_edited / total * 100, 1) if total > 0 else 0.0,
            "categories": categories,
        }

    # ── Listing Drafts ──

    def create_listing_draft(self, data: dict[str, Any]) -> dict[str, Any]:
        result = self._client.table("listing_drafts").insert(data).execute()
        return result.data[0] if result.data else {}

    def get_listing_draft(self, draft_id: int) -> dict[str, Any] | None:
        result = (
            self._client.table("listing_drafts")
            .select("*")
            .eq("id", draft_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def update_listing_draft_status(
        self, draft_id: int, status: str, **extra_fields
    ) -> dict[str, Any]:
        result = (
            self._client.table("listing_drafts")
            .update({"status": status, **extra_fields})
            .eq("id", draft_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    # ── Products ──

    def upsert_product(self, product_data: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a product by SKU."""
        result = (
            self._client.table("products")
            .upsert(product_data, on_conflict="sku")
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_product_by_sku(self, sku: str) -> dict[str, Any] | None:
        result = (
            self._client.table("products")
            .select("*")
            .eq("sku", sku)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_all_products(self) -> list[dict[str, Any]]:
        result = (
            self._client.table("products")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    def get_all_active_products(self) -> list[dict[str, Any]]:
        """Products with a Shopify ID (published/synced). Used for catalog feeds."""
        result = (
            self._client.table("products")
            .select("*")
            .not_.is_("shopify_product_id", "null")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    def delete_product(self, sku: str) -> None:
        self._client.table("products").delete().eq("sku", sku).execute()

    def update_product_images(self, sku: str, image_urls: list[str]) -> dict[str, Any]:
        """Overwrite the images column for a product. Used by Shopify image sync."""
        result = (
            self._client.table("products")
            .update({"images": image_urls})
            .eq("sku", sku)
            .execute()
        )
        return result.data[0] if result.data else {}

    # ── Ad Creatives (Phase 6.1) ──

    def create_generation_batch(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a generation_batches row. Upserts on idempotency_key to prevent duplicates."""
        result = (
            self._client.table("generation_batches")
            .upsert(data, on_conflict="idempotency_key")
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_generation_batch(self, batch_id: str) -> dict[str, Any] | None:
        result = (
            self._client.table("generation_batches")
            .select("*")
            .eq("id", batch_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def update_generation_batch_status(
        self, batch_id: str, status: str, **extra_fields
    ) -> dict[str, Any]:
        result = (
            self._client.table("generation_batches")
            .update({"status": status, **extra_fields})
            .eq("id", batch_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def create_ad_creative_batch(self, variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Atomic-ish batch insert for ad_creatives rows. Either all succeed or none (supabase-py
        insert(list) is a single SQL statement so partial insert is not possible on the server
        side — any constraint violation rolls back the entire call).
        """
        if not variants:
            return []
        result = self._client.table("ad_creatives").insert(variants).execute()
        return result.data or []

    def get_ad_creative(self, creative_id: int) -> dict[str, Any] | None:
        result = (
            self._client.table("ad_creatives")
            .select("*")
            .eq("id", creative_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_ad_creatives_by_status(self, status: str, limit: int = 60) -> list[dict[str, Any]]:
        result = (
            self._client.table("ad_creatives")
            .select("*")
            .eq("status", status)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    def get_ad_creatives_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        result = (
            self._client.table("ad_creatives")
            .select("*")
            .eq("generation_batch_id", batch_id)
            .order("variant_label", desc=False)
            .execute()
        )
        return result.data or []

    def get_recent_ad_creatives(self, limit: int = 60) -> list[dict[str, Any]]:
        """All ad_creatives, newest first. Used by the dashboard list page."""
        result = (
            self._client.table("ad_creatives")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    def approve_ad_creative_atomic(
        self, creative_id: int, approved_by: str = ""
    ) -> dict[str, Any] | None:
        """Atomic transition pending_review → publishing. Returns row on success, None if the
        creative was not in pending_review state (already approved, rejected, or in-flight).

        This is the race-condition fix: two concurrent approves will see the second one return
        None and the dashboard can show "already being processed".
        """
        from datetime import datetime

        result = (
            self._client.table("ad_creatives")
            .update({
                "status": "publishing",
                "approved_by": approved_by or "dashboard",
                "approved_at": datetime.utcnow().isoformat(),
            })
            .eq("id", creative_id)
            .eq("status", "pending_review")
            .execute()
        )
        return result.data[0] if result.data else None

    def mark_ad_creative_published(
        self, creative_id: int, meta_creative_id: str
    ) -> dict[str, Any]:
        result = (
            self._client.table("ad_creatives")
            .update({
                "status": "published",
                "meta_creative_id": meta_creative_id,
            })
            .eq("id", creative_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def revert_ad_creative_to_pending(self, creative_id: int) -> dict[str, Any]:
        """Rollback if Meta push fails after atomic transition to 'publishing'."""
        result = (
            self._client.table("ad_creatives")
            .update({"status": "pending_review", "approved_by": None, "approved_at": None})
            .eq("id", creative_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def reject_ad_creative(self, creative_id: int) -> dict[str, Any]:
        result = (
            self._client.table("ad_creatives")
            .update({"status": "rejected"})
            .eq("id", creative_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def pause_ad_creative(self, creative_id: int) -> dict[str, Any]:
        result = (
            self._client.table("ad_creatives")
            .update({"status": "paused"})
            .eq("id", creative_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def set_ad_creative_published_from_paused(self, creative_id: int) -> dict[str, Any]:
        """Used when founder clicks Go Live on a previously-paused creative."""
        result = (
            self._client.table("ad_creatives")
            .update({"status": "published"})
            .eq("id", creative_id)
            .execute()
        )
        return result.data[0] if result.data else {}

    def set_ad_creative_live(
        self,
        creative_id: int,
        meta_ad_id: str | None = None,
        meta_adset_id: str | None = None,
    ) -> dict[str, Any]:
        """Transition a `published` (PAUSED on Meta) creative to `live` (ACTIVE on Meta).

        Called after a successful Meta UPDATE status=ACTIVE. Without this, the dashboard
        keeps showing the stale 'Paused on Meta' badge even though Meta's side flipped.

        Phase 6.2: also persists the Meta Ad ID + Ad Set ID created during Go Live,
        so the dashboard can deep-link to the Ad in Ads Manager.
        """
        updates: dict[str, Any] = {"status": "live"}
        if meta_ad_id:
            updates["meta_ad_id"] = meta_ad_id
        if meta_adset_id:
            updates["meta_adset_id"] = meta_adset_id
        result = (
            self._client.table("ad_creatives")
            .update(updates)
            .eq("id", creative_id)
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

    def mark_abandoned_carts(self, delay_minutes: int = 60) -> int:
        """Transition 'created' carts older than delay_minutes to 'abandoned'.

        Returns the number of carts marked as abandoned.
        """
        cutoff = (datetime.utcnow() - timedelta(minutes=delay_minutes)).isoformat()
        result = (
            self._client.table("cart_events")
            .update({"event_type": "abandoned"})
            .eq("event_type", "created")
            .lt("created_at", cutoff)
            .execute()
        )
        return len(result.data) if result.data else 0

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
        """Net revenue = SUM(total) - SUM(refund_amount), excluding cancelled orders."""
        result = (
            self._client.table("orders")
            .select("total, refund_amount")
            .gte("created_at", start_date.isoformat())
            .lte("created_at", end_date.isoformat())
            .neq("status", "cancelled")
            .execute()
        )
        if not result.data:
            return 0.0
        return sum(
            float(r["total"]) - float(r.get("refund_amount") or 0)
            for r in result.data
        )

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


class AsyncDatabase:
    """Async wrapper around Database. Delegates all calls via asyncio.to_thread().

    Each instance creates its own Database (no shared singleton) to avoid
    thread-safety issues when multiple async handlers run concurrently.

    Usage:
        db = AsyncDatabase()
        order = await db.get_order_by_shopify_id(12345)
    """

    def __init__(self):
        self._sync = Database()

    def __getattr__(self, name: str):
        attr = getattr(self._sync, name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        async def async_wrapper(*args, **kwargs):
            return await asyncio.to_thread(attr, *args, **kwargs)

        return async_wrapper
