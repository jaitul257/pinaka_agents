"""ContextAssembler — cross-module context for agent reasoning.

Pulls data from multiple modules so agents see the full picture when
making decisions. Reuses existing AsyncDatabase methods — no new queries.
"""

import logging
from typing import Any

from src.core.database import AsyncDatabase

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Assemble cross-module context for agent runs."""

    def __init__(self):
        self._db = AsyncDatabase()

    async def for_order(self, shopify_order_id: int) -> dict[str, Any]:
        """Full context for an order: order + customer + messages + shipping.

        Used by: OrderOpsAgent, CustomerServiceAgent
        """
        context: dict[str, Any] = {}

        # Order details
        order = await self._db.get_order_by_shopify_id(shopify_order_id)
        if order:
            context["order"] = order
            context["order_total"] = order.get("total")

            # Customer record
            buyer_email = order.get("buyer_email")
            if buyer_email:
                customer = await self._db.get_customer_by_email(buyer_email)
                if customer:
                    context["customer"] = customer
                    context["last_reorder_email_at"] = customer.get("last_reorder_email_at")

                    # Customer's order history
                    customer_id = customer.get("id")
                    if customer_id:
                        orders = await self._db.get_orders_by_customer(customer_id)
                        context["order_history"] = orders
                        context["order_count"] = len(orders)

        return context

    async def for_customer(self, email: str) -> dict[str, Any]:
        """Full context for a customer: profile + orders + messages + eligibility.

        Used by: CustomerServiceAgent, RetentionAgent
        """
        context: dict[str, Any] = {}

        customer = await self._db.get_customer_by_email(email)
        if not customer:
            context["customer_found"] = False
            return context

        context["customer"] = customer
        context["customer_found"] = True
        context["last_reorder_email_at"] = customer.get("last_reorder_email_at")

        # Order history
        customer_id = customer.get("id")
        if customer_id:
            orders = await self._db.get_orders_by_customer(customer_id)
            context["order_history"] = orders
            context["order_count"] = len(orders)

            # Lifetime value
            total_spent = sum(float(o.get("total", 0)) for o in orders)
            context["lifetime_value"] = round(total_spent, 2)

        return context

    async def for_message(
        self, message: str, sender_email: str
    ) -> dict[str, Any]:
        """Context for an inbound customer message.

        Used by: CustomerServiceAgent
        """
        context = await self.for_customer(sender_email)
        context["inbound_message"] = message
        context["sender_email"] = sender_email
        return context

    async def for_daily_ops(self) -> dict[str, Any]:
        """Daily operations context: today's stats, pending items, ad performance.

        Used by: FinanceAgent, MarketingAgent
        """
        from datetime import date, timedelta

        context: dict[str, Any] = {}

        today = date.today()
        week_ago = today - timedelta(days=7)

        # Daily stats for the past week
        stats = await self._db.get_stats_range(week_ago, today)
        context["daily_stats"] = stats

        # Pending messages
        pending = await self._db.get_pending_messages()
        context["pending_messages"] = len(pending)

        # Abandoned carts
        carts = await self._db.get_abandoned_carts_pending_recovery()
        context["abandoned_carts"] = len(carts)

        # Orders by status
        paid_orders = await self._db.get_orders_by_status("paid")
        shipped_orders = await self._db.get_orders_by_status("shipped")
        context["orders_awaiting_fulfillment"] = len(paid_orders)
        context["orders_in_transit"] = len(shipped_orders)

        return context

    async def for_retention(self, customer_id: int) -> dict[str, Any]:
        """Context for retention decisions (reorder reminders, cart recovery).

        Used by: RetentionAgent
        """
        context: dict[str, Any] = {}

        orders = await self._db.get_orders_by_customer(customer_id)
        context["order_history"] = orders
        context["order_count"] = len(orders)

        if orders:
            latest = max(orders, key=lambda o: o.get("created_at", ""))
            context["last_order"] = latest
            context["last_order_date"] = latest.get("created_at")

        return context
