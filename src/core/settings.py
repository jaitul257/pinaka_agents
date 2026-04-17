"""Pinaka Agents — centralized configuration via Pydantic Settings.

All thresholds, intervals, toggles, and budget caps are env vars with defaults.
Validated at startup. Single source of truth for the entire application.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── Shopify ──
    shopify_shop_domain: str = ""  # e.g. "pinaka-jewellery.myshopify.com" — used for Admin API
    shopify_storefront_url: str = ""  # e.g. "pinakajewellery.com" — customer-facing custom domain
    shopify_api_key: str = ""
    shopify_api_secret: str = ""
    shopify_access_token: str = ""  # Admin API access token
    shopify_webhook_secret: str = ""  # HMAC verification
    shopify_api_version: str = "2026-01"

    # ── SendGrid ──
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = ""  # Authenticated sender
    sendgrid_from_name: str = ""  # e.g. "Jaitul at Pinaka Jewellery"
    sendgrid_cart_recovery_template_id: str = ""
    sendgrid_service_reply_template_id: str = ""
    sendgrid_crafting_update_template_id: str = ""
    sendgrid_order_confirmation_template_id: str = ""
    sendgrid_shipping_notification_template_id: str = ""
    sendgrid_delivery_confirmation_template_id: str = ""
    sendgrid_refund_confirmation_template_id: str = ""
    sendgrid_reorder_reminder_template_id: str = ""
    # Phase 9.2: post-purchase lifecycle + welcome series
    sendgrid_lifecycle_template_id: str = ""
    sendgrid_welcome_1_template_id: str = ""
    sendgrid_welcome_2_template_id: str = ""
    sendgrid_welcome_3_template_id: str = ""
    sendgrid_welcome_4_template_id: str = ""
    sendgrid_welcome_5_template_id: str = ""

    # Phase 9.3: Shopify blog (for weekly SEO post auto-publish) + Pinterest API
    shopify_blog_id: str = ""  # Shopify blog to publish drafts into (numeric ID)
    pinterest_access_token: str = ""  # from Pinterest Developer Portal
    pinterest_board_id: str = ""  # target board for auto-generated pins
    pinterest_ad_account_id: str = ""  # optional, for tag conversion tracking
    pinterest_tag_id: str = ""  # public pinterest tag ID for theme pixel

    # ── Anthropic ──
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    daily_token_budget: int = 500_000

    # ── OpenAI (embeddings) ──
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    # ── Supabase ──
    supabase_url: str = ""
    supabase_key: str = ""

    # ── ShipStation ──
    shipstation_api_key: str = ""
    shipstation_api_secret: str = ""
    shipstation_base_url: str = "https://ssapi.shipstation.com"
    shipstation_webhook_secret: str = ""  # Shared secret for inbound webhook validation

    # ── Slack ──
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # ── Sentry ──
    sentry_dsn: str = ""

    # ── Freepik ──
    freepik_api_key: str = ""

    # ── Meta (Conversions API) ──
    meta_pixel_id: str = ""
    meta_capi_access_token: str = ""
    meta_graph_api_version: str = "v25.0"  # Meta deprecates ~quarterly; bump when (#2635) errors appear

    # ── Webhooks ──
    webhook_base_url: str = ""  # Railway public URL, e.g. "https://pinaka-agents-production-198b5.up.railway.app"

    # ── Security ──
    cron_secret: str = ""
    dashboard_password: str = ""

    # ── Application ──
    founder_name: str = "Pinaka Jewellery"
    log_level: str = "INFO"
    made_to_order_days: int = 15  # "Ships in X business days"
    business_timezone: str = "US/Eastern"  # Canonical TZ for daily_stats date boundaries

    # ── Meta Marketing ──
    meta_ad_account_id: str = ""  # Format: act_XXXXXXXXX (from Meta Ads Manager)
    meta_ads_access_token: str = ""  # Long-lived token with ads_read scope (separate from CAPI token)
    meta_catalog_id: str = ""  # Product Catalog ID from Commerce Manager
    meta_business_id: str = ""  # Business ID from Business Settings
    meta_facebook_page_id: str = ""  # Required for /act_{id}/adcreatives API (Phase 6.1 ad creative push)
    meta_app_id: str = ""  # Pinaka Marketing app ID (for future appsecret_proof if enabled)
    meta_app_secret: str = ""  # App secret (stored but not required unless "Require App Secret" is toggled on)
    # Phase 6.2 — pre-created containers that hold auto-generated Ads
    meta_default_campaign_id: str = ""  # OUTCOME_SALES campaign (PAUSED by default, user flips once)
    meta_default_adset_id: str = ""  # Ad Set with targeting/budget/$25 cap (PAUSED by default)

    # ── Google Ads ──
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_customer_id: str = ""  # 10-digit, no dashes
    google_ads_conversion_action_id: str = ""  # From Conversions setup

    # ── Google Merchant Center ──
    google_merchant_id: str = ""
    google_service_account_path: str = ""  # Local path to JSON key
    google_service_account_json: str = ""  # JSON content (for Railway)

    # ── Module thresholds ──
    # Shipping
    insurance_required_above: float = 500.0
    carrier_insurance_cap: float = 2500.0
    high_value_threshold: float = 5000.0
    velocity_max_orders_24h: int = 2

    # Marketing
    max_daily_ad_budget: float = 75.0  # Google Shopping + Meta combined
    roas_increase_threshold: float = 4.0
    roas_maintain_min: float = 2.0
    roas_window_days: int = 30

    # Customer service
    crafting_update_delay_days: int = 3  # Days after order to send crafting update
    abandoned_cart_delay_minutes: int = 60  # Minutes before cart counts as abandoned
    max_cart_recovery_emails_per_week: int = 2  # Per customer

    # Reorder reminders
    reorder_reminder_days: str = "90,180,365"  # Days after purchase to check (comma-separated)
    reorder_cooldown_days: int = 180  # Min days between reorder emails per customer

    # Agent system
    agent_enabled: bool = False  # Feature flag — flip on Railway when ready
    agent_max_turns: int = 15  # Max reasoning loop iterations per agent run

    # Rate limiting
    shopify_qps: float = 2.0  # Shopify Admin API: 2 requests/second (basic plan)
    claude_rpm: int = 50
    shipstation_qps: float = 2.0
    sendgrid_qps: float = 1.0

    @property
    def shopify_admin_url(self) -> str:
        """Base URL for Shopify Admin API."""
        return f"https://{self.shopify_shop_domain}/admin/api/{self.shopify_api_version}"

    @property
    def storefront_domain(self) -> str:
        """Customer-facing domain (custom domain preferred, falls back to myshopify)."""
        return self.shopify_storefront_url or self.shopify_shop_domain

    @property
    def is_meta_creative_ready(self) -> bool:
        """True when Phase 6.1 ad creative push to Meta is fully configured.

        When False, the dashboard disables Approve buttons and shows a warning banner
        explaining which env var is missing (typically META_FACEBOOK_PAGE_ID).
        """
        return bool(
            self.meta_ads_access_token
            and self.meta_ad_account_id
            and self.meta_facebook_page_id
        )

    @property
    def is_meta_ad_ready(self) -> bool:
        """True when Phase 6.2 Ad object auto-creation is fully configured.

        Requires everything in is_meta_creative_ready PLUS a default Ad Set to attach
        new Ads to. When False, "Go Live" still flips creative status but skips Ad
        creation (so the flow stays backwards-compatible).
        """
        return self.is_meta_creative_ready and bool(self.meta_default_adset_id)


settings = Settings()
