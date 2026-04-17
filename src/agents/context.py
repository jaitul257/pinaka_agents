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

            # Customer record + memory
            buyer_email = order.get("buyer_email")
            if buyer_email:
                customer_context = await self.for_customer(buyer_email)
                context.update(customer_context)

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

        # Past message history (customer memory across agent runs)
        try:
            messages = await self._db._sync._client.table("messages") \
                .select("category, status, created_at, ai_draft") \
                .eq("customer_email", email) \
                .order("created_at", desc=True) \
                .limit(10) \
                .execute()
            if messages.data:
                context["past_interactions"] = [
                    {
                        "date": m.get("created_at", "")[:10],
                        "category": m.get("category", ""),
                        "status": m.get("status", ""),
                        "summary": (m.get("ai_draft") or "")[:100],
                    }
                    for m in messages.data
                ]
                context["interaction_count"] = len(messages.data)
        except Exception:
            pass  # Non-critical — customer memory is a nice-to-have

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

    # ── Internal helpers ──
    # These return *slices*, not dumps. Public for_<agent> methods compose
    # only the slices their agent actually reasons on. Rule: if an agent
    # never cites the field in its output, it shouldn't be in its context.

    async def _daily_stats(self, days: int = 7) -> list[dict[str, Any]]:
        from datetime import date, timedelta
        today = date.today()
        return await self._db.get_stats_range(today - timedelta(days=days), today)

    async def _recent_order_margins(self, cap: int = 20) -> dict[str, Any]:
        """Per-order margins for the most recent paid/shipped/delivered orders.

        Returns {product_margins, avg_margin_pct, total_net_profit} or empty.
        Marketing needs margin data to prioritize ad spend; finance needs it
        for profit reporting. Nobody else should see this.
        """
        from src.finance.calculator import FinanceCalculator

        try:
            finance = FinanceCalculator()
            all_orders: list[dict[str, Any]] = []
            for status in ("paid", "shipped", "delivered"):
                orders = await self._db.get_orders_by_status(status)
                all_orders.extend(orders)
            if not all_orders:
                return {}
            margins = []
            for order in all_orders[:cap]:
                profit = finance.calculate_order_profit(order)
                margins.append({
                    "order_id": order.get("shopify_order_id"),
                    "revenue": profit.revenue,
                    "net_profit": profit.net_profit,
                    "margin_pct": profit.margin_pct,
                })
            avg = sum(m["margin_pct"] for m in margins) / len(margins)
            return {
                "product_margins": margins,
                "avg_margin_pct": round(avg, 1),
                "total_net_profit": round(sum(m["net_profit"] for m in margins), 2),
            }
        except Exception:
            logger.exception("_recent_order_margins failed")
            return {}

    async def for_marketing(self) -> dict[str, Any]:
        """Marketing-only context: daily stats + margin data.

        Does NOT include abandoned carts, pending messages, or fulfillment
        counts — those belong to other agents and would just invite the
        marketing agent to correlate noise. Seasonal window is fetched via
        the `get_current_strategy` tool, not the context dump.
        """
        context: dict[str, Any] = {"daily_stats": await self._daily_stats()}
        context.update(await self._recent_order_margins())
        return context

    async def for_finance(self) -> dict[str, Any]:
        """Finance-only context: daily stats + margin data over 30 days.

        Same slice as marketing today; diverges as Phase 13's outcome
        feedback lands. Kept as a separate method so the divergence doesn't
        require refactoring callers.
        """
        context: dict[str, Any] = {"daily_stats": await self._daily_stats(days=30)}
        context.update(await self._recent_order_margins(cap=30))
        return context

    async def for_order_ops(self) -> dict[str, Any]:
        """Order ops context: only the fulfillment queue.

        Does NOT include ad spend, ROAS, margins, or pending customer
        messages. Order ops cares about physical movement of orders.
        """
        paid = await self._db.get_orders_by_status("paid")
        shipped = await self._db.get_orders_by_status("shipped")
        return {
            "orders_awaiting_fulfillment": len(paid),
            "orders_in_transit": len(shipped),
            # Include the IDs so the agent can reason about specific orders
            # without needing a second tool call.
            "awaiting_order_ids": [o.get("shopify_order_id") for o in paid[:20]],
        }

    async def for_customer_service_queue(self) -> dict[str, Any]:
        """Customer service queue context: pending messages + abandoned carts.

        Used when the CS agent runs a queue sweep (not a single-message
        reply — that uses for_message). Does NOT include revenue or margin
        data.
        """
        pending = await self._db.get_pending_messages()
        carts = await self._db.get_abandoned_carts_pending_recovery()
        return {
            "pending_message_count": len(pending),
            "abandoned_cart_count": len(carts),
        }

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
