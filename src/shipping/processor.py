"""Shipping processor with ShipStation integration and fraud detection.

Handles ShipStation order creation, rate fetching, tracking lookups,
insurance validation, and fraud checks (high-value orders, velocity detection).
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
    """Process orders for shipping with fraud checks and ShipStation integration."""

    def __init__(self):
        self._db = Database()
        self._slack = SlackNotifier()
        self._shipstation = RateLimitedClient(
            base_url=settings.shipstation_base_url,
            qps=settings.shipstation_qps,
            headers={"Content-Type": "application/json"},
            auth=(settings.shipstation_api_key, settings.shipstation_api_secret)
            if settings.shipstation_api_key
            else None,
        )

    # ── ShipStation API ──

    async def create_shipstation_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        """Create an order in ShipStation from a Shopify order.

        Maps Shopify order fields to ShipStation's /orders/createorder format.
        Returns the ShipStation order response or error dict.
        """
        if not settings.shipstation_api_key:
            logger.warning("ShipStation API key not configured, skipping order push")
            return {"skipped": True, "reason": "no_api_key"}

        shopify_order_id = order_data.get("shopify_order_id") or order_data.get("id")
        shipping_addr = order_data.get("shipping_address", {})
        billing_addr = order_data.get("billing_address", shipping_addr)
        line_items = order_data.get("line_items", [])

        # Map to ShipStation order format
        ss_order = {
            "orderNumber": str(order_data.get("order_number", shopify_order_id)),
            "orderKey": str(shopify_order_id),
            "orderDate": order_data.get("created_at", ""),
            "orderStatus": "awaiting_shipment",
            "customerEmail": order_data.get("buyer_email", order_data.get("email", "")),
            "amountPaid": float(order_data.get("total", 0)),
            "taxAmount": float(order_data.get("tax", 0)),
            "shippingAmount": float(order_data.get("shipping_cost", 0)),
            "billTo": {
                "name": billing_addr.get("name", order_data.get("buyer_name", "")),
                "street1": billing_addr.get("address1", ""),
                "street2": billing_addr.get("address2", ""),
                "city": billing_addr.get("city", ""),
                "state": billing_addr.get("province_code", billing_addr.get("province", "")),
                "postalCode": billing_addr.get("zip", ""),
                "country": billing_addr.get("country_code", billing_addr.get("country", "US")),
                "phone": billing_addr.get("phone", ""),
            },
            "shipTo": {
                "name": shipping_addr.get("name", order_data.get("buyer_name", "")),
                "street1": shipping_addr.get("address1", ""),
                "street2": shipping_addr.get("address2", ""),
                "city": shipping_addr.get("city", ""),
                "state": shipping_addr.get("province_code", shipping_addr.get("province", "")),
                "postalCode": shipping_addr.get("zip", ""),
                "country": shipping_addr.get("country_code", shipping_addr.get("country", "US")),
                "phone": shipping_addr.get("phone", ""),
            },
            "items": [
                {
                    "lineItemKey": str(item.get("id", "")),
                    "sku": item.get("sku", ""),
                    "name": item.get("title", item.get("name", "Item")),
                    "quantity": int(item.get("quantity", 1)),
                    "unitPrice": float(item.get("price", 0)),
                    "weight": {
                        "value": float(item.get("grams", 0)) / 28.3495 if item.get("grams") else 1.0,
                        "units": "ounces",
                    },
                }
                for item in line_items
            ],
            "advancedOptions": {
                "storeId": None,
                "customField1": f"Shopify #{shopify_order_id}",
            },
            "insuranceOptions": {
                "provider": "carrier" if float(order_data.get("total", 0)) <= settings.carrier_insurance_cap else "shipsurance",
                "insureShipment": float(order_data.get("total", 0)) >= settings.insurance_required_above,
                "insuredValue": float(order_data.get("total", 0)),
            },
        }

        response = await self._shipstation.post("/orders/createorder", json=ss_order)

        if response.status_code in (200, 201):
            result = response.json()
            ss_order_id = result.get("orderId")
            logger.info(
                "ShipStation order created: %s (SS ID: %s)",
                shopify_order_id, ss_order_id,
            )
            return result
        else:
            error_msg = response.text
            logger.error(
                "ShipStation order creation failed for %s: %s %s",
                shopify_order_id, response.status_code, error_msg,
            )
            return {"error": True, "status_code": response.status_code, "detail": error_msg}

    async def get_shipping_rates(self, order_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch available shipping rates from ShipStation for an order.

        Returns a list of rate options with carrier, service, price.
        """
        if not settings.shipstation_api_key:
            return []

        shipping_addr = order_data.get("shipping_address", {})
        total_weight_oz = sum(
            float(item.get("grams", 0)) / 28.3495 if item.get("grams") else 1.0
            for item in order_data.get("line_items", [])
        )

        rate_request = {
            "carrierCode": "",  # Empty = all carriers
            "fromPostalCode": settings.ship_from_zip if hasattr(settings, "ship_from_zip") else "10001",
            "toState": shipping_addr.get("province_code", shipping_addr.get("province", "")),
            "toCountry": shipping_addr.get("country_code", shipping_addr.get("country", "US")),
            "toPostalCode": shipping_addr.get("zip", ""),
            "toCity": shipping_addr.get("city", ""),
            "weight": {
                "value": max(total_weight_oz, 1.0),
                "units": "ounces",
            },
            "confirmation": "delivery",
            "residential": True,
        }

        response = await self._shipstation.post("/shipments/getrates", json=rate_request)

        if response.status_code == 200:
            rates = response.json()
            logger.info("Got %d shipping rates", len(rates))
            return [
                {
                    "carrier": r.get("carrierCode", ""),
                    "service": r.get("serviceName", ""),
                    "service_code": r.get("serviceCode", ""),
                    "price": r.get("shipmentCost", 0) + r.get("otherCost", 0),
                    "days": r.get("estimatedDeliveryDate", ""),
                }
                for r in rates
            ]
        else:
            logger.error("Failed to get rates: %s %s", response.status_code, response.text)
            return []

    async def get_tracking(self, shipstation_order_id: int) -> dict[str, Any]:
        """Get tracking info for a ShipStation order.

        Fetches shipments for the order and returns tracking details.
        """
        if not settings.shipstation_api_key:
            return {"error": "no_api_key"}

        response = await self._shipstation.get(
            f"/orders/{shipstation_order_id}",
        )

        if response.status_code != 200:
            return {"error": f"Failed to fetch order: {response.status_code}"}

        order = response.json()
        ss_order_id = order.get("orderId")

        # Fetch shipments for this order
        shipments_resp = await self._shipstation.get(
            "/shipments", params={"orderId": ss_order_id},
        )

        if shipments_resp.status_code != 200:
            return {"error": f"Failed to fetch shipments: {shipments_resp.status_code}"}

        data = shipments_resp.json()
        shipments = data.get("shipments", [])

        if not shipments:
            return {
                "status": "no_shipments",
                "order_status": order.get("orderStatus", "unknown"),
            }

        latest = shipments[0]
        return {
            "status": "shipped",
            "tracking_number": latest.get("trackingNumber", ""),
            "carrier": latest.get("carrierCode", ""),
            "service": latest.get("serviceCode", ""),
            "ship_date": latest.get("shipDate", ""),
            "delivery_date": latest.get("deliveryDate", ""),
            "label_cost": latest.get("shipmentCost", 0),
        }

    async def list_carriers(self) -> list[dict[str, str]]:
        """List all carriers configured in ShipStation account."""
        if not settings.shipstation_api_key:
            return []

        response = await self._shipstation.get("/carriers")
        if response.status_code == 200:
            return [
                {"code": c.get("code", ""), "name": c.get("name", "")}
                for c in response.json()
            ]
        return []

    # ── Fraud Detection ──

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
        """Process a new order: fraud check, insurance validation, push to ShipStation."""
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

        # Push to ShipStation
        ss_result = await self.create_shipstation_order(order)
        if ss_result.get("error"):
            logger.error("ShipStation push failed for order %s: %s", order_id, ss_result)
            # Don't block the order, just log it
            self._db.update_order_status(order_id, "ready_to_ship")
            return {"status": "ready_to_ship", "shipstation": "failed"}

        self._db.update_order_status(order_id, "ready_to_ship")
        return {
            "status": "ready_to_ship",
            "shipstation_order_id": ss_result.get("orderId"),
        }

    # ── Tracking Webhook Handler ──

    async def handle_tracking_update(self, resource_url: str, resource_type: str) -> dict[str, Any]:
        """Process a ShipStation tracking webhook.

        ShipStation webhooks only send a resource_url. We GET it to fetch full details,
        then update order status, send emails, and notify Slack.
        """
        from src.core.email import EmailSender

        # Fetch full shipment/order details from ShipStation
        response = await self._shipstation.get(resource_url.replace(settings.shipstation_base_url, ""))
        if response.status_code != 200:
            logger.error("Failed to fetch ShipStation resource %s: %s", resource_url, response.status_code)
            return {"error": f"Failed to fetch resource: {response.status_code}"}

        data = response.json()

        # Extract tracking info (works for both shipment and order resources)
        if resource_type == "SHIP_NOTIFY" or resource_type == "ITEM_SHIP_NOTIFY":
            tracking_number = data.get("trackingNumber", "")
            carrier = data.get("carrierCode", "")
            order_key = data.get("orderKey", "")
            order_number = data.get("orderNumber", "")
            ship_date = data.get("shipDate", "")
        else:
            # ORDER_NOTIFY: data is the order object
            tracking_number = ""
            carrier = ""
            order_key = data.get("orderKey", "")
            order_number = data.get("orderNumber", "")
            ship_date = ""
            # Check shipments within order
            shipments = data.get("shipments", [])
            if shipments:
                latest = shipments[0]
                tracking_number = latest.get("trackingNumber", "")
                carrier = latest.get("carrierCode", "")
                ship_date = latest.get("shipDate", "")

        if not order_key:
            logger.warning("ShipStation webhook missing orderKey")
            return {"error": "missing_order_key"}

        # orderKey was set to shopify_order_id in create_shipstation_order()
        try:
            shopify_order_id = int(order_key)
        except (ValueError, TypeError):
            logger.warning("Non-integer orderKey from ShipStation: %s", order_key)
            return {"error": "invalid_order_key"}

        # Look up order in Supabase
        order = self._db.get_order_by_shopify_id(shopify_order_id)
        if not order:
            logger.warning("Order not found for ShipStation webhook: %s", shopify_order_id)
            return {"error": "order_not_found"}

        previous_status = order.get("status", "")

        # Build tracking URL
        tracking_url = ""
        if tracking_number:
            carrier_lower = carrier.lower()
            if "fedex" in carrier_lower:
                tracking_url = f"https://www.fedex.com/fedextrack/?trknbr={tracking_number}"
            elif "ups" in carrier_lower:
                tracking_url = f"https://www.ups.com/track?tracknum={tracking_number}"
            elif "usps" in carrier_lower or "stamps" in carrier_lower:
                tracking_url = f"https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking_number}"
            else:
                tracking_url = f"https://track.aftership.com/{tracking_number}"

        # Update order in Supabase
        if tracking_number and previous_status != "delivered":
            from datetime import datetime as dt
            update_fields = {
                "shipped_at": ship_date or dt.utcnow().isoformat(),
            }
            self._db.update_order_tracking(
                shopify_order_id=shopify_order_id,
                tracking_number=tracking_number,
                carrier=carrier,
                status="shipped",
                tracking_url=tracking_url,
                **update_fields,
            )

            # Send shipping notification email (only on first ship, not re-sends)
            if previous_status != "shipped":
                buyer_email = order.get("buyer_email", "")
                buyer_name = order.get("buyer_name", "")
                if buyer_email:
                    email = EmailSender()
                    email.send_shipping_notification(
                        to_email=buyer_email,
                        customer_name=buyer_name or buyer_email,
                        order_number=order_number or str(shopify_order_id),
                        tracking_number=tracking_number,
                        carrier=carrier,
                        tracking_url=tracking_url,
                    )

                # Slack notification
                await self._slack.send_shipping_update(
                    order_number=order_number or str(shopify_order_id),
                    customer_name=order.get("buyer_name", "") or order.get("buyer_email", ""),
                    tracking_number=tracking_number,
                    carrier=carrier,
                    tracking_url=tracking_url,
                )

                # Update Shopify fulfillment status
                from src.core.shopify_client import ShopifyClient
                shopify = ShopifyClient()
                try:
                    await shopify.create_fulfillment(
                        order_id=shopify_order_id,
                        tracking_number=tracking_number,
                        tracking_company=carrier,
                    )
                    logger.info("Shopify fulfillment created for order #%s", shopify_order_id)
                except Exception:
                    logger.exception("Shopify fulfillment failed for order #%s", shopify_order_id)
                finally:
                    await shopify.close()

        result = {
            "shopify_order_id": shopify_order_id,
            "tracking_number": tracking_number,
            "carrier": carrier,
            "tracking_url": tracking_url,
            "previous_status": previous_status,
            "new_status": "shipped",
        }

        logger.info(
            "Tracking update for order #%s: %s via %s",
            shopify_order_id, tracking_number, carrier,
        )
        return result

    async def close(self) -> None:
        await self._shipstation.close()
