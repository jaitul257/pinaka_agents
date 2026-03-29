"""Pinaka Jewellery — Streamlit Monitoring Dashboard.

6 sections: Overview, Orders, Customers, Finance, Marketing, Customer Service.
Auth gate via DASHBOARD_PASSWORD. Styled per DESIGN.md (warm cream + saffron).
"""

import hmac
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# ── Page Config (must be first Streamlit call) ──
st.set_page_config(
    page_title="Pinaka Dashboard",
    page_icon="💎",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DESIGN.md Theming ──
THEME_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600;700&family=DM+Sans:wght@300;400;500;600;700&family=Geist+Mono:wght@300;400;500&display=swap');

    :root {
        --bg: #FAF7F2;
        --surface: #FFFFFF;
        --surface-raised: #F5F0E8;
        --text-primary: #2C2825;
        --text-secondary: #6B6560;
        --text-muted: #9E9893;
        --accent: #D4A017;
        --accent-hover: #B8890F;
        --accent-subtle: rgba(212, 160, 23, 0.12);
        --gold: #C5A55A;
        --gold-light: rgba(197, 165, 90, 0.15);
        --border: #E8E2D9;
        --border-light: #F0EBE3;
        --success: #2E7D4F;
        --success-bg: rgba(46, 125, 79, 0.08);
        --warning: #C17E1A;
        --warning-bg: rgba(193, 126, 26, 0.08);
        --error: #C4392D;
        --error-bg: rgba(196, 57, 45, 0.08);
        --info: #3B7EC5;
        --info-bg: rgba(59, 126, 197, 0.08);
    }

    .stApp {
        background-color: var(--bg) !important;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: var(--surface) !important;
        border-right: 1px solid var(--border) !important;
    }

    /* Headers */
    h1, h2, h3 {
        font-family: 'Cormorant Garamond', serif !important;
        color: var(--text-primary) !important;
    }
    h1 { font-weight: 400 !important; }
    h2 { font-weight: 500 !important; }
    h3 { font-weight: 600 !important; font-family: 'DM Sans', sans-serif !important; }

    /* Body text */
    p, span, label, div {
        font-family: 'DM Sans', sans-serif !important;
        color: var(--text-secondary) !important;
    }

    /* Metric values */
    [data-testid="stMetricValue"] {
        font-family: 'Geist Mono', monospace !important;
        color: var(--text-primary) !important;
    }
    [data-testid="stMetricLabel"] {
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 600 !important;
        font-size: 11px !important;
        text-transform: uppercase !important;
        letter-spacing: 1px !important;
        color: var(--text-muted) !important;
    }
    [data-testid="stMetricDelta"] {
        font-family: 'Geist Mono', monospace !important;
    }

    /* Cards/containers */
    [data-testid="stExpander"], .stAlert {
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
    }

    /* Buttons */
    .stButton > button {
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 500 !important;
        border-radius: 8px !important;
        border: 1px solid var(--border) !important;
        transition: all 150ms ease-out !important;
    }
    .stButton > button:hover {
        border-color: var(--accent) !important;
        color: var(--accent) !important;
    }
    .stButton > button[kind="primary"] {
        background-color: var(--accent) !important;
        border-color: var(--accent) !important;
        color: var(--text-primary) !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: var(--accent-hover) !important;
    }

    /* Data tables */
    .stDataFrame {
        font-family: 'Geist Mono', monospace !important;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab"] {
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 500 !important;
    }
    .stTabs [aria-selected="true"] {
        border-bottom-color: var(--accent) !important;
        color: var(--accent) !important;
    }

    /* Divider */
    hr {
        border-color: var(--border-light) !important;
    }

    /* Gold divider accent */
    .gold-divider {
        height: 1px;
        background: linear-gradient(90deg, transparent, var(--gold), transparent);
        margin: 24px 0;
    }

    /* Status badges */
    .badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 9999px;
        font-size: 12px;
        font-weight: 600;
        font-family: 'DM Sans', sans-serif;
    }
    .badge-success { background: var(--success-bg); color: var(--success); }
    .badge-warning { background: var(--warning-bg); color: var(--warning); }
    .badge-error { background: var(--error-bg); color: var(--error); }
    .badge-info { background: var(--info-bg); color: var(--info); }

    /* Stat card styling */
    .stat-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 1px 3px rgba(44, 40, 37, 0.06);
    }
</style>
"""

st.markdown(THEME_CSS, unsafe_allow_html=True)


# ── Auth Gate ──

def check_password() -> bool:
    """Simple password gate using DASHBOARD_PASSWORD env var."""
    from src.core.settings import settings

    if not settings.dashboard_password:
        return True  # No password set, allow access (dev mode)

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.markdown("# Pinaka Jewellery")
    st.markdown("*Dashboard Login*")
    password = st.text_input("Password", type="password", key="password_input")
    if st.button("Enter", type="primary"):
        if hmac.compare_digest(password, settings.dashboard_password):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    return False


if not check_password():
    st.stop()


# ── Data Loading (cached) ──

from src.core.database import Database
from src.finance.calculator import FinanceCalculator


@st.cache_data(ttl=300)
def load_overview_data():
    """Load overview stats. Cached for 5 minutes."""
    db = Database()
    today = date.today()
    week_ago = today - timedelta(days=7)
    prev_week_start = week_ago - timedelta(days=7)

    this_week_stats = db.get_stats_range(week_ago, today)
    prev_week_stats = db.get_stats_range(prev_week_start, week_ago)
    pending_msgs = db.get_pending_messages()

    this_revenue = sum(float(s.get("revenue", 0)) for s in this_week_stats)
    prev_revenue = sum(float(s.get("revenue", 0)) for s in prev_week_stats)
    this_orders = sum(int(s.get("order_count", 0)) for s in this_week_stats)
    prev_orders = sum(int(s.get("order_count", 0)) for s in prev_week_stats)
    new_customers = sum(int(s.get("new_customers", 0)) for s in this_week_stats)

    customer_count = db.get_customer_count()
    repeat_count = db.get_repeat_customer_count()
    abandoned = db.get_abandoned_carts_pending_recovery()

    return {
        "this_revenue": this_revenue,
        "prev_revenue": prev_revenue,
        "this_orders": this_orders,
        "prev_orders": prev_orders,
        "new_customers": new_customers,
        "customer_count": customer_count,
        "repeat_count": repeat_count,
        "pending_messages": len(pending_msgs),
        "urgent_messages": len([m for m in pending_msgs if m.get("urgency") == "urgent"]),
        "abandoned_carts": len(abandoned),
        "daily_stats": this_week_stats,
    }


@st.cache_data(ttl=300)
def load_orders_data():
    """Load recent orders."""
    db = Database()
    statuses = ["paid", "approved_for_shipping", "held_for_review", "crafting_update_sent", "ready_to_ship"]
    all_orders = []
    for status in statuses:
        all_orders.extend(db.get_orders_by_status(status))
    return all_orders


@st.cache_data(ttl=300)
def load_customers_data():
    """Load customer lifecycle breakdown."""
    db = Database()
    stages = ["lead", "first_purchase", "repeat", "advocate"]
    result = {}
    for stage in stages:
        customers = db.get_customers_by_lifecycle(stage)
        result[stage] = customers
    return result


@st.cache_data(ttl=300)
def load_finance_data():
    """Load finance stats for the last 30 days."""
    db = Database()
    today = date.today()
    month_ago = today - timedelta(days=30)
    return db.get_stats_range(month_ago, today)


@st.cache_data(ttl=300)
def load_marketing_data():
    """Load marketing/ads stats for the last 30 days."""
    db = Database()
    today = date.today()
    month_ago = today - timedelta(days=30)
    return db.get_stats_range(month_ago, today)


@st.cache_data(ttl=300)
def load_messages_data():
    """Load customer messages."""
    db = Database()
    return db.get_pending_messages()


# ── Sidebar ──

st.sidebar.markdown("# 💎 Pinaka")
st.sidebar.markdown("*Diamond Tennis Bracelets*")
st.sidebar.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Orders", "Customers", "Finance", "Marketing", "Customer Service"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"<small style='color: var(--text-muted);'>Last refreshed: {date.today()}</small>",
    unsafe_allow_html=True,
)
if st.sidebar.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()


# ── Helper Functions ──

def delta_str(current: float, previous: float) -> str | None:
    """Return a delta string for metrics, or None if no previous data."""
    if previous == 0:
        return None
    pct = ((current - previous) / previous) * 100
    return f"{pct:+.1f}%"


def status_badge(status: str) -> str:
    """Return HTML for a colored status badge."""
    colors = {
        "paid": "success",
        "approved_for_shipping": "success",
        "ready_to_ship": "success",
        "crafting_update_sent": "info",
        "held_for_review": "warning",
        "fraud_review": "error",
        "cancelled": "error",
        "pending_review": "warning",
        "sent": "success",
        "rejected": "error",
    }
    badge_type = colors.get(status, "info")
    label = status.replace("_", " ").title()
    return f'<span class="badge badge-{badge_type}">{label}</span>'


# ── Pages ──

if page == "Overview":
    st.markdown("# Dashboard")
    st.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

    try:
        data = load_overview_data()

        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric(
                "Revenue (7d)",
                f"${data['this_revenue']:,.2f}",
                delta=delta_str(data["this_revenue"], data["prev_revenue"]),
            )
        with col2:
            st.metric(
                "Orders (7d)",
                data["this_orders"],
                delta=delta_str(data["this_orders"], data["prev_orders"]),
            )
        with col3:
            st.metric("New Customers (7d)", data["new_customers"])
        with col4:
            st.metric("Total Customers", data["customer_count"])
        with col5:
            repeat_rate = (data["repeat_count"] / data["customer_count"] * 100) if data["customer_count"] > 0 else 0
            st.metric("Repeat Rate", f"{repeat_rate:.1f}%")

        # Second row
        col1, col2, col3 = st.columns(3)
        with col1:
            aov = data["this_revenue"] / data["this_orders"] if data["this_orders"] > 0 else 0
            st.metric("Avg Order Value", f"${aov:,.2f}")
        with col2:
            st.metric(
                "Pending Messages",
                data["pending_messages"],
                delta=f"{data['urgent_messages']} urgent" if data["urgent_messages"] else None,
                delta_color="inverse",
            )
        with col3:
            st.metric("Abandoned Carts", data["abandoned_carts"])

        st.markdown("### Revenue Trend")
        if data["daily_stats"]:
            df = pd.DataFrame(data["daily_stats"])
            if "date" in df.columns and "revenue" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df["revenue"] = df["revenue"].astype(float)
                st.line_chart(df.set_index("date")["revenue"])
            else:
                st.info("Revenue data columns not available yet.")
        else:
            st.info("No data for the last 7 days. Orders will appear once Shopify webhooks fire.")

        # Agent status
        st.markdown("### Agent Status")
        agents = [
            {"Agent": "Shopify Webhooks", "Module": "webhooks", "Status": "Active", "Last Run": "—"},
            {"Agent": "Shipping & Fraud", "Module": "shipping", "Status": "Active", "Last Run": "—"},
            {"Agent": "Cart Recovery", "Module": "cart_recovery", "Status": "Active", "Last Run": "—"},
            {"Agent": "Customer Service", "Module": "customer", "Status": "Active", "Last Run": "—"},
            {"Agent": "Finance", "Module": "finance", "Status": "Active", "Last Run": "—"},
        ]
        st.dataframe(pd.DataFrame(agents), use_container_width=True, hide_index=True)

    except Exception as e:
        st.warning(f"Could not load overview data. Is the database configured? ({e})")
        st.info("Set SUPABASE_URL and SUPABASE_KEY in .env to connect.")


elif page == "Orders":
    st.markdown("# Orders")
    st.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

    try:
        orders = load_orders_data()

        if orders:
            # Summary metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Orders", len(orders))
            with col2:
                total_rev = sum(float(o.get("total", 0)) for o in orders)
                st.metric("Total Revenue", f"${total_rev:,.2f}")
            with col3:
                held = len([o for o in orders if o.get("status") == "held_for_review"])
                st.metric("Held for Review", held)

            # Filters
            statuses = sorted(set(o.get("status", "unknown") for o in orders))
            selected_status = st.selectbox("Filter by status", ["All"] + statuses)

            filtered = orders
            if selected_status != "All":
                filtered = [o for o in orders if o.get("status") == selected_status]

            # Table
            df = pd.DataFrame(filtered)
            display_cols = ["shopify_order_id", "buyer_name", "buyer_email", "total", "status", "created_at"]
            available_cols = [c for c in display_cols if c in df.columns]
            if available_cols:
                display_df = df[available_cols].copy()
                if "total" in display_df.columns:
                    display_df["total"] = display_df["total"].apply(lambda x: f"${float(x):,.2f}")
                st.dataframe(display_df, use_container_width=True, hide_index=True)
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No orders yet. They'll appear once Shopify webhooks start firing.")

    except Exception as e:
        st.warning(f"Could not load orders. ({e})")


elif page == "Customers":
    st.markdown("# Customers")
    st.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

    try:
        customers_by_stage = load_customers_data()

        # Lifecycle funnel
        col1, col2, col3, col4 = st.columns(4)
        stages = [("lead", col1), ("first_purchase", col2), ("repeat", col3), ("advocate", col4)]
        for stage, col in stages:
            with col:
                count = len(customers_by_stage.get(stage, []))
                st.metric(stage.replace("_", " ").title(), count)

        # Customer list by lifecycle
        selected_stage = st.selectbox(
            "Filter by lifecycle stage",
            ["All", "lead", "first_purchase", "repeat", "advocate"],
        )

        if selected_stage == "All":
            all_customers = []
            for stage_customers in customers_by_stage.values():
                all_customers.extend(stage_customers)
        else:
            all_customers = customers_by_stage.get(selected_stage, [])

        if all_customers:
            df = pd.DataFrame(all_customers)
            display_cols = ["name", "email", "lifecycle_stage", "order_count", "lifetime_value", "created_at"]
            available_cols = [c for c in display_cols if c in df.columns]
            if available_cols:
                display_df = df[available_cols].copy()
                if "lifetime_value" in display_df.columns:
                    display_df["lifetime_value"] = display_df["lifetime_value"].apply(
                        lambda x: f"${float(x):,.2f}" if x else "$0.00"
                    )
                st.dataframe(display_df, use_container_width=True, hide_index=True)
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No customers in this lifecycle stage yet.")

    except Exception as e:
        st.warning(f"Could not load customer data. ({e})")


elif page == "Finance":
    st.markdown("# Finance")
    st.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

    try:
        stats = load_finance_data()

        if stats:
            df = pd.DataFrame(stats)
            numeric_cols = ["revenue", "cogs", "shopify_fees", "shipping_cost", "ad_spend", "net_profit"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            # Summary cards
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                total_rev = df["revenue"].sum() if "revenue" in df.columns else 0
                st.metric("Revenue (30d)", f"${total_rev:,.2f}")
            with col2:
                total_profit = df["net_profit"].sum() if "net_profit" in df.columns else 0
                st.metric("Net Profit (30d)", f"${total_profit:,.2f}")
            with col3:
                margin = (total_profit / total_rev * 100) if total_rev > 0 else 0
                st.metric("Avg Margin", f"{margin:.1f}%")
            with col4:
                total_fees = df["shopify_fees"].sum() if "shopify_fees" in df.columns else 0
                st.metric("Shopify Fees (30d)", f"${total_fees:,.2f}")

            # Revenue vs Profit chart
            st.markdown("### Revenue vs Profit")
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                chart_df = df.set_index("date")
                chart_cols = [c for c in ["revenue", "net_profit"] if c in chart_df.columns]
                if chart_cols:
                    st.line_chart(chart_df[chart_cols])

            # Fee breakdown
            st.markdown("### Fee Breakdown")
            fee_cols = [c for c in ["shopify_fees", "shipping_cost", "ad_spend", "cogs"] if c in df.columns]
            if fee_cols:
                fee_totals = {col.replace("_", " ").title(): df[col].sum() for col in fee_cols}
                fee_df = pd.DataFrame(
                    [{"Category": k, "Amount": f"${v:,.2f}"} for k, v in fee_totals.items()]
                )
                st.dataframe(fee_df, use_container_width=True, hide_index=True)

            # Shopify fee calculator
            st.markdown("### Fee Calculator")
            calc = FinanceCalculator.__new__(FinanceCalculator)
            calc_price = st.number_input("Order total ($)", value=2850.0, step=50.0)
            fees = calc.calculate_shopify_fees(calc_price)
            net = calc_price - fees
            fee_rate = (fees / calc_price * 100) if calc_price > 0 else 0
            st.markdown(f"**Shopify fees:** ${fees:.2f}  |  **After fees:** ${net:.2f}  |  **Fee rate:** {fee_rate:.1f}%")

        else:
            st.info("No finance data for the last 30 days.")

    except Exception as e:
        st.warning(f"Could not load finance data. ({e})")


elif page == "Marketing":
    st.markdown("# Marketing & Ads")
    st.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

    try:
        stats = load_marketing_data()

        if stats:
            df = pd.DataFrame(stats)
            for col in ["ad_spend_google", "ad_spend_meta", "ad_revenue"]:
                if col in df.columns:
                    df[col] = df[col].astype(float)

            total_google = df["ad_spend_google"].sum() if "ad_spend_google" in df.columns else 0
            total_meta = df["ad_spend_meta"].sum() if "ad_spend_meta" in df.columns else 0
            total_spend = total_google + total_meta
            total_ad_rev = df["ad_revenue"].sum() if "ad_revenue" in df.columns else 0
            roas = total_ad_rev / total_spend if total_spend > 0 else 0

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Ad Spend (30d)", f"${total_spend:,.2f}")
            with col2:
                st.metric("Ad Revenue (30d)", f"${total_ad_rev:,.2f}")
            with col3:
                st.metric("ROAS", f"{roas:.2f}x")
            with col4:
                from src.core.settings import settings
                st.metric("Daily Budget Cap", f"${settings.max_daily_ad_budget:.2f}")

            # Spend breakdown
            st.markdown("### Spend by Channel")
            spend_df = pd.DataFrame([
                {"Channel": "Google Shopping", "Spend": f"${total_google:,.2f}"},
                {"Channel": "Meta Ads", "Spend": f"${total_meta:,.2f}"},
            ])
            st.dataframe(spend_df, use_container_width=True, hide_index=True)

            # ROAS gauge
            st.markdown("### ROAS Performance")
            if roas >= 4.0:
                st.success(f"ROAS {roas:.2f}x — Above increase threshold (4.0x). Consider increasing budget.")
            elif roas >= 2.0:
                st.info(f"ROAS {roas:.2f}x — Healthy. Maintain current budget.")
            elif roas > 0:
                st.warning(f"ROAS {roas:.2f}x — Below target (2.0x). Consider reducing budget.")
            else:
                st.error("No ad revenue tracked. Ads may be paused or data not synced.")

        else:
            st.info("No marketing data for the last 30 days.")

        # ROAS thresholds reference
        st.markdown("### Budget Thresholds")
        from src.core.settings import settings
        thresholds = pd.DataFrame([
            {"Metric": "Increase budget when ROAS above", "Value": f"{settings.roas_increase_threshold}x"},
            {"Metric": "Maintain budget when ROAS above", "Value": f"{settings.roas_maintain_min}x"},
            {"Metric": "Max daily budget", "Value": f"${settings.max_daily_ad_budget:.2f}"},
            {"Metric": "ROAS window", "Value": f"{settings.roas_window_days} days"},
        ])
        st.dataframe(thresholds, use_container_width=True, hide_index=True)

    except Exception as e:
        st.warning(f"Could not load marketing data. ({e})")


elif page == "Customer Service":
    st.markdown("# Customer Service")
    st.markdown('<div class="gold-divider"></div>', unsafe_allow_html=True)

    try:
        messages = load_messages_data()

        # Summary
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Pending Messages", len(messages))
        with col2:
            urgent = [m for m in messages if m.get("urgency") == "urgent"]
            st.metric("Urgent", len(urgent))
        with col3:
            categories = {}
            for m in messages:
                cat = m.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1
            top_cat = max(categories, key=categories.get) if categories else "—"
            st.metric("Top Category", top_cat.replace("_", " ").title())

        if urgent:
            st.markdown("### Urgent Messages")
            for msg in urgent:
                with st.expander(
                    f"🔴 {msg.get('buyer_name', 'Unknown')} — {msg.get('category', 'unknown').replace('_', ' ')}",
                    expanded=True,
                ):
                    st.markdown(f"**Message:** {msg.get('body', '')}")
                    if msg.get("ai_draft"):
                        st.markdown(f"**AI Draft:** {msg['ai_draft']}")
                    st.caption(f"Message #{msg.get('id', '—')} | {msg.get('customer_email', '')}")

        if messages:
            st.markdown("### All Pending Messages")
            df = pd.DataFrame(messages)
            display_cols = ["buyer_name", "customer_email", "category", "body", "urgency", "status"]
            available_cols = [c for c in display_cols if c in df.columns]
            if available_cols:
                st.dataframe(df[available_cols], use_container_width=True, hide_index=True)
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)

            # Category breakdown
            if categories:
                st.markdown("### Message Categories")
                cat_df = pd.DataFrame(
                    [{"Category": k.replace("_", " ").title(), "Count": v}
                     for k, v in sorted(categories.items(), key=lambda x: -x[1])]
                )
                st.bar_chart(cat_df.set_index("Category"))
        else:
            st.info("No pending messages. Customer emails arrive via SendGrid Inbound Parse.")

        # Configuration reference
        st.markdown("### Configuration")
        from src.core.settings import settings
        config = pd.DataFrame([
            {"Setting": "Made-to-order lead time", "Value": f"{settings.made_to_order_days} days"},
            {"Setting": "Crafting update delay", "Value": f"{settings.crafting_update_delay_days} days"},
            {"Setting": "Cart abandonment timer", "Value": f"{settings.abandoned_cart_delay_minutes} min"},
            {"Setting": "Max recovery emails/week", "Value": str(settings.max_cart_recovery_emails_per_week)},
        ])
        st.dataframe(config, use_container_width=True, hide_index=True)

    except Exception as e:
        st.warning(f"Could not load customer data. ({e})")
