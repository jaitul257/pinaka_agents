"""Profit calculator and financial reporting for Pinaka Jewellery.

Computes per-order profit, daily P&L, and generates weekly finance summaries.
Cost data comes from product schema (COGS), Shopify fees are calculated from
the standard Shopify fee schedule, and shipping costs come from order records.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.core.database import Database
from src.core.settings import settings
from src.core.slack import SlackNotifier

logger = logging.getLogger(__name__)

# Shopify fee schedule (Basic plan, 2025)
SHOPIFY_PAYMENT_PROCESSING_RATE = 0.029  # 2.9% of order total
SHOPIFY_PAYMENT_PROCESSING_FIXED = 0.30  # + $0.30 per transaction
SHOPIFY_PLAN_TRANSACTION_FEE = 0.0  # 0% when using Shopify Payments (2% otherwise)


@dataclass
class OrderProfit:
    """Profit breakdown for a single order."""

    shopify_order_id: int
    revenue: float
    cogs: float
    shopify_fees: float
    shipping_cost: float
    ad_spend: float
    net_profit: float
    margin_pct: float


@dataclass
class DailyFinanceSummary:
    """Aggregated daily P&L."""

    date: date
    total_revenue: float
    total_cogs: float
    total_shopify_fees: float
    total_shipping: float
    total_ad_spend: float
    total_net_profit: float
    order_count: int
    avg_order_value: float
    avg_margin_pct: float


@dataclass
class WeeklyFinanceReport:
    """Weekly finance report with daily breakdown."""

    start_date: date
    end_date: date
    daily_summaries: list[DailyFinanceSummary] = field(default_factory=list)
    total_revenue: float = 0.0
    total_cogs: float = 0.0
    total_fees: float = 0.0
    total_shipping: float = 0.0
    total_ad_spend: float = 0.0
    total_net_profit: float = 0.0
    total_orders: int = 0
    avg_margin_pct: float = 0.0


class FinanceCalculator:
    """Calculate profit metrics and generate financial reports."""

    def __init__(self):
        self._db = Database()
        self._slack = SlackNotifier()

    def calculate_shopify_fees(self, order_total: float) -> float:
        """Calculate Shopify payment processing fees for an order.

        Assumes Shopify Payments (no extra transaction fee).
        """
        processing = order_total * SHOPIFY_PAYMENT_PROCESSING_RATE + SHOPIFY_PAYMENT_PROCESSING_FIXED
        return round(processing, 2)

    def calculate_order_profit(self, order: dict[str, Any]) -> OrderProfit:
        """Calculate profit for a single order.

        order dict should have: shopify_order_id, total, cogs (cost of goods),
        shipping_cost, ad_spend (optional).
        """
        revenue = float(order.get("total", 0))
        cogs = float(order.get("cogs", 0))
        shipping_cost = float(order.get("shipping_cost", 0))
        ad_spend = float(order.get("ad_spend", 0))

        shopify_fees = self.calculate_shopify_fees(revenue)

        net_profit = revenue - cogs - shopify_fees - shipping_cost - ad_spend
        margin_pct = (net_profit / revenue * 100) if revenue > 0 else 0.0

        return OrderProfit(
            shopify_order_id=order.get("shopify_order_id", 0),
            revenue=round(revenue, 2),
            cogs=round(cogs, 2),
            shopify_fees=round(shopify_fees, 2),
            shipping_cost=round(shipping_cost, 2),
            ad_spend=round(ad_spend, 2),
            net_profit=round(net_profit, 2),
            margin_pct=round(margin_pct, 1),
        )

    def summarize_daily(
        self, orders: list[dict[str, Any]], for_date: date
    ) -> DailyFinanceSummary:
        """Aggregate order profits into a daily summary."""
        if not orders:
            return DailyFinanceSummary(
                date=for_date,
                total_revenue=0,
                total_cogs=0,
                total_shopify_fees=0,
                total_shipping=0,
                total_ad_spend=0,
                total_net_profit=0,
                order_count=0,
                avg_order_value=0,
                avg_margin_pct=0,
            )

        profits = [self.calculate_order_profit(o) for o in orders]

        total_revenue = sum(p.revenue for p in profits)
        total_cogs = sum(p.cogs for p in profits)
        total_fees = sum(p.shopify_fees for p in profits)
        total_shipping = sum(p.shipping_cost for p in profits)
        total_ad = sum(p.ad_spend for p in profits)
        total_net = sum(p.net_profit for p in profits)

        return DailyFinanceSummary(
            date=for_date,
            total_revenue=round(total_revenue, 2),
            total_cogs=round(total_cogs, 2),
            total_shopify_fees=round(total_fees, 2),
            total_shipping=round(total_shipping, 2),
            total_ad_spend=round(total_ad, 2),
            total_net_profit=round(total_net, 2),
            order_count=len(orders),
            avg_order_value=round(total_revenue / len(orders), 2),
            avg_margin_pct=round(
                sum(p.margin_pct for p in profits) / len(profits), 1
            ),
        )

    async def run_weekly_finance_report(self) -> WeeklyFinanceReport:
        """Generate a weekly P&L report and send to Slack."""
        end = date.today()
        start = end - timedelta(days=7)

        daily_stats = self._db.get_stats_range(start, end)

        report = WeeklyFinanceReport(start_date=start, end_date=end)
        for stat in daily_stats:
            summary = DailyFinanceSummary(
                date=date.fromisoformat(stat["date"]),
                total_revenue=float(stat.get("revenue", 0)),
                total_cogs=float(stat.get("cogs", 0)),
                total_shopify_fees=float(stat.get("shopify_fees", 0)),
                total_shipping=float(stat.get("shipping_cost", 0)),
                total_ad_spend=float(stat.get("ad_spend", 0)),
                total_net_profit=float(stat.get("net_profit", 0)),
                order_count=int(stat.get("order_count", 0)),
                avg_order_value=float(stat.get("avg_order_value", 0)),
                avg_margin_pct=float(stat.get("avg_margin_pct", 0)),
            )
            report.daily_summaries.append(summary)

        report.total_revenue = sum(d.total_revenue for d in report.daily_summaries)
        report.total_cogs = sum(d.total_cogs for d in report.daily_summaries)
        report.total_fees = sum(d.total_shopify_fees for d in report.daily_summaries)
        report.total_shipping = sum(d.total_shipping for d in report.daily_summaries)
        report.total_ad_spend = sum(d.total_ad_spend for d in report.daily_summaries)
        report.total_net_profit = sum(d.total_net_profit for d in report.daily_summaries)
        report.total_orders = sum(d.order_count for d in report.daily_summaries)
        if report.daily_summaries:
            report.avg_margin_pct = round(
                sum(d.avg_margin_pct for d in report.daily_summaries)
                / len(report.daily_summaries),
                1,
            )

        await self._send_weekly_slack(report)
        logger.info(
            "Weekly finance: revenue=$%.2f, profit=$%.2f, margin=%.1f%%, orders=%d",
            report.total_revenue,
            report.total_net_profit,
            report.avg_margin_pct,
            report.total_orders,
        )
        return report

    async def _send_weekly_slack(self, report: WeeklyFinanceReport) -> None:
        """Send weekly finance summary to Slack."""
        profit_emoji = ":money_with_wings:" if report.total_net_profit > 0 else ":warning:"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{profit_emoji} WEEKLY P&L ({report.start_date} to {report.end_date})",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Revenue:* ${report.total_revenue:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Orders:* {report.total_orders}"},
                    {"type": "mrkdwn", "text": f"*COGS:* ${report.total_cogs:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Shopify Fees:* ${report.total_fees:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Shipping:* ${report.total_shipping:,.2f}"},
                    {"type": "mrkdwn", "text": f"*Ad Spend:* ${report.total_ad_spend:,.2f}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Net Profit:* ${report.total_net_profit:,.2f}",
                    },
                    {"type": "mrkdwn", "text": f"*Avg Margin:* {report.avg_margin_pct}%"},
                ],
            },
        ]

        await self._slack.send_blocks(
            blocks,
            text=f"Weekly P&L: ${report.total_net_profit:,.2f} profit ({report.avg_margin_pct}% margin)",
        )
