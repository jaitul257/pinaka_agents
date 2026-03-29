"""Shipping processor with ShipStation integration and fraud detection.

Handles label generation, insurance validation, and fraud checks
(high-value orders, velocity detection).
"""

import logging
from dataclasses import dataclass
from typing import Any

from src.core.database import Database
from src.core.rate_limiter import RateLimitedClient
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)


@dataclass
class FraudCheckResult:
    is_flagged: bool
    reasons: list[str]
    requires_video_verification: bool = False
    insurance_gap: float = 0.0


class ShippingProcessor:
    """Process orders for shipping with fraud checks and insurance validation."""

    def __init__(self):
        self._db = Database()
        self._slack = SlackNotifier()
        self._shipstation = RateLimitedClient(
            base_url=settings.shipstation_base_url,
            qps=settings.shipstation_qps,
            headers={
                "Content-Type": "application/json",
            },
        )

    def check_fraud(self, order: dict[str, Any]) -> FraudCheckResult:
        """Run fraud detection checks on an order."""
        reasons = []
        total = float(order.get("total", 0))
        buyer_email = order.get("buyer_email", "")

        # High-value threshold
        if total > settings.high_value_threshold:
            reasons.append(
                f"High value (${total:,.2f} > ${settings.high_value_threshold:,.2f} threshold)"
            )

        # Velocity check
        recent_count = self._db.count_orders_from_email_24h(buyer_email)
        if recent_count >= settings.velocity_max_orders_24h:
            reasons.append(
                f"Velocity: {recent_count + 1} orders from same buyer in 24h "
                f"(threshold: {settings.velocity_max_orders_24h})"
            )

        # Insurance gap check
        insurance_gap = 0.0
        if total > settings.carrier_insurance_cap:
            insurance_gap = total - settings.carrier_insurance_cap
            reasons.append(
                f"Insurance gap: carrier caps at ${settings.carrier_insurance_cap:,.2f}, "
                f"order is ${total:,.2f} (gap: ${insurance_gap:,.2f})"
            )

        return FraudCheckResult(
            is_flagged=len(reasons) > 0,
            reasons=reasons,
            requires_video_verification=total > settings.high_value_threshold,
            insurance_gap=insurance_gap,
        )

    def validate_insurance(self, order_total: float) -> dict[str, Any]:
        """Check if carrier insurance covers the order value."""
        if order_total <= settings.carrier_insurance_cap:
            return {
                "covered": True,
                "insured_value": order_total,
                "gap": 0.0,
            }

        return {
            "covered": False,
            "insured_value": settings.carrier_insurance_cap,
            "gap": order_total - settings.carrier_insurance_cap,
            "action_required": "Arrange supplemental jewelry floater insurance before shipping",
        }

    async def process_order(self, order: dict[str, Any]) -> dict[str, str]:
        """Process a new order: fraud check, insurance validation, label generation."""
        order_id = order.get("shopify_order_id") or order.get("id")
        total = float(order.get("total", 0))

        # Fraud check
        fraud_result = self.check_fraud(order)
        if fraud_result.is_flagged:
            insurance_note = ""
            if fraud_result.insurance_gap > 0:
                insurance_note = (
                    f"Carrier cap: ${settings.carrier_insurance_cap:,.2f}. "
                    f"Gap: ${fraud_result.insurance_gap:,.2f}. Supplemental required."
                )

            await self._slack.send_fraud_alert(
                receipt_id=order_id,
                buyer_name=order.get("buyer_name", "Unknown"),
                total=total,
                flag_reason=" | ".join(fraud_result.reasons),
                insurance_note=insurance_note,
            )

            self._db.update_order_status(order_id, "fraud_review")
            return {"status": "fraud_review", "reasons": fraud_result.reasons}

        # Insurance validation
        insurance = self.validate_insurance(total)
        if not insurance["covered"]:
            await self._slack.send_alert(
                f"Order #{order_id} (${total:,.2f}) exceeds carrier insurance cap. "
                f"Gap: ${insurance['gap']:,.2f}. Arrange supplemental insurance before shipping.",
                level="warning",
            )
            self._db.update_order_status(order_id, "insurance_hold")
            return {"status": "insurance_hold", "gap": insurance["gap"]}

        # All clear — queue for label generation
        self._db.update_order_status(order_id, "ready_to_ship")
        return {"status": "ready_to_ship"}

    async def close(self) -> None:
        await self._shipstation.close()
