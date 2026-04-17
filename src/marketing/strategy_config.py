"""Marketing strategy configuration — data, not prompt.

Previously this lived inside MarketingAgent's system prompt (~85 lines of
baked-in rules). Moving it here so:

  • The agent can call a tool and reason against RETURNED data (current
    truth), not a memorized memo that may be stale.
  • Editing the budget cap or seasonal window doesn't require editing
    a prompt string and hoping Claude picks it up.
  • The same config can drive the dashboard, the weekly-strategy cron,
    and the agent — one source of truth.

Nothing in here is secret; if any value needs to vary by environment,
move it to src/core/settings.py and let this module read it.
"""

from __future__ import annotations

from datetime import date
from typing import Any


# Campaign allocation — retargeting-heavy at high AOV is the real win
# at 1-2 orders/week with a long consideration window. Warm pools convert
# 5-10x better per dollar than cold, so flip from the naive prospecting-heavy
# default.
CAMPAIGNS: list[dict[str, Any]] = [
    {
        "name": "retargeting",
        "tier": "warm",
        "daily_budget_usd": 35,
        "allocation_pct": 47,
        "audiences": [
            "site visitors 1-7d ($18)",
            "site visitors 8-30d ($17)",
            "IG/FB engagers + 75% video viewers (1-60d)",
        ],
        "exclusions": ["all purchasers"],
        "kpis": {"atc_rate_min_pct": 3.0, "ic_rate_min_pct": 1.5, "mer_min": 4.0},
    },
    {
        "name": "prospecting",
        "tier": "cold",
        "daily_budget_usd": 30,
        "allocation_pct": 40,
        "audiences": [
            "broad US women 28-55, no interest stacking",
            "1% value-based LAL of top-quartile ATC",
            "1% LAL of high-value visitors",
        ],
        "exclusions": ["past purchasers", "180d visitors"],
        "optimize_for": "ADD_TO_CART",
        "kpis": {"cpm_max_usd": 25, "ctr_min_pct": 1.2, "cpc_max_usd": 3, "atc_rate_min_pct": 1.0},
    },
    {
        "name": "retention",
        "tier": "hot",
        "daily_budget_usd": 10,
        "allocation_pct": 13,
        "audiences": ["past purchasers", "email subscribers", "anniversary segment"],
        "exclusions": [],
        "kpis": {"mer_min": 6.0, "metrics_to_track": ["repeat_purchase_rate", "referral_clicks"]},
    },
]

BUDGET_RULES: dict[str, Any] = {
    "daily_cap_usd": 75,           # Meta + Google combined
    "auto_adjust_tolerance_usd": 5,  # agent may change within ±$5 without approval
    "hard_max_usd": 150,            # 2x cap — requires seasonal + founder approval
    "min_per_adset_usd": 20,        # below this, ad sets can't exit learning phase
    "cost_cap_cpa_usd": 2000,       # prospecting cost cap
}

# Measurement hierarchy — platform ROAS lies at our volume. Use these in
# order of trust, not just the first number that's handy.
MEASUREMENT_TRUST_ORDER: list[str] = [
    "mer_14d",                    # total revenue / total ad spend over 14d+ window
    "post_purchase_survey",       # post_purchase_attribution table (weekly synthesis)
    "platform_roas_directional",  # Meta/Google — signal only, not a budget decision
]

# Anchors we always want on ad sets we control. Exceptions are a red flag.
ACCOUNT_DEFAULTS: dict[str, Any] = {
    "optimization_goal": "ADD_TO_CART",     # Purchase can't exit learning at 1-2/week
    "attribution_spec": "28d_click_1d_view",  # consideration cycle is 14-45d
    "bidding": "lowest_cost_with_cost_cap",
    "never_use": ["manual_bid"],
}

# When to increase budget 2-3x. Founder approval still required to exceed
# the hard_max_usd cap.
SEASONAL_CALENDAR: list[dict[str, Any]] = [
    {"name": "Valentine's Day",              "start": (1, 15),  "end": (2, 14),  "angle": "Gift for her, self-purchase. 'Handcrafted with love.'"},
    {"name": "Mother's Day",                 "start": (4, 15),  "end": (5, 11),  "angle": "'She deserves handcrafted.' Gift that lasts generations."},
    {"name": "Anniversary/Wedding Season",   "start": (5, 1),   "end": (6, 30),  "angle": "Bridal, milestone anniversary gifts."},
    {"name": "Black Friday / Cyber Monday",  "start": (11, 15), "end": (12, 2),  "angle": "Early access, gift-with-purchase (never discount — luxury brand)."},
    {"name": "Holiday Gifting",              "start": (12, 1),  "end": (12, 20), "angle": "Lead time urgency: 'Order by Dec X for holiday delivery.'"},
    {"name": "New Year Self-Purchase",       "start": (1, 1),   "end": (1, 14),  "angle": "'Start the year brilliant.' Self-reward positioning."},
]

CREATIVE_STRATEGY: dict[str, Any] = {
    "rotation_weeks": (2, 3),
    "types": [
        "hero lifestyle video (15-30s) — wrist close-up, natural light, sparkle",
        "craftsmanship story (carousel/video) — hand-setting diamonds, workshop",
        "social proof static — customer photo + quote overlay",
        "product on clean background + price anchor — direct response for retargeting",
    ],
    "video_to_static_ratio_pct": (60, 40),
    "fatigue_threshold_pct": 30,  # CTR drop week-over-week
}

MARGIN_RULES: dict[str, Any] = {
    "prioritize_above_pct": 40,
    "flag_below_pct": 20,
    "alert_if_negative": True,  # pause ads + Slack alert
}


def check_seasonal_window(today: date | None = None) -> dict[str, Any] | None:
    """Return the active seasonal window today, or None.

    If the window ends <=7 days from today we bump the budget multiplier
    from 2.0 to 2.5 (scarcity tailwind).
    """
    today = today or date.today()
    for window in SEASONAL_CALENDAR:
        start = date(today.year, window["start"][0], window["start"][1])
        end = date(today.year, window["end"][0], window["end"][1])
        if start <= today <= end:
            days_left = (end - today).days
            return {
                "name": window["name"],
                "angle": window["angle"],
                "days_left": days_left,
                "budget_multiplier": 2.5 if days_left <= 7 else 2.0,
            }
    return None


def snapshot() -> dict[str, Any]:
    """Single entry point for the `get_current_strategy` tool.

    Bundles everything the agent needs to reason about allocation, budget,
    and measurement trust. Kept flat + small so it doesn't bloat context.
    """
    return {
        "campaigns": CAMPAIGNS,
        "budget_rules": BUDGET_RULES,
        "measurement_trust_order": MEASUREMENT_TRUST_ORDER,
        "account_defaults": ACCOUNT_DEFAULTS,
        "creative_strategy": CREATIVE_STRATEGY,
        "margin_rules": MARGIN_RULES,
        "active_seasonal_window": check_seasonal_window(),
    }
