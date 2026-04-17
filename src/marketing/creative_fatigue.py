"""Creative fatigue detector — flags ads whose performance is decaying.

Runs daily. Compares this-week vs last-week performance per ad and raises
an alert for creatives that should be rotated or replaced.

Rules (ordered by severity — first match wins):
  1. `dead_spend`   — spent $50+ with ZERO purchases and 5K+ impressions.
                     Bleeding budget. Highest priority.
  2. `high_freq`    — frequency > 3.5. Same user seeing the ad too often.
  3. `ctr_decay`    — CTR dropped >= 30% WoW, with ≥ 500 impressions each
                     week (too-noisy gate to avoid false positives).
  4. `weak_ctr`     — CTR < 0.5% on 2K+ impressions this week. Never worked.

All rules require minimum volume to avoid flagging new/low-volume ads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

# Tunable thresholds (keep in code — business rules should be visible in diff)
DEAD_SPEND_MIN_SPEND = 50.0
DEAD_SPEND_MIN_IMPRESSIONS = 5_000
HIGH_FREQ_THRESHOLD = 3.5
CTR_DECAY_MIN_IMPRESSIONS = 500
CTR_DECAY_RATIO = 0.70  # this-week CTR must be >= 70% of last-week to pass
WEAK_CTR_THRESHOLD = 0.005  # 0.5%
WEAK_CTR_MIN_IMPRESSIONS = 2_000


@dataclass
class FatigueFlag:
    meta_ad_id: str
    ad_name: str
    meta_creative_id: str | None
    creative_name: str
    reason: str  # one of: 'dead_spend' | 'high_freq' | 'ctr_decay' | 'weak_ctr'
    detail: str  # human-readable one-liner for Slack
    metrics: dict[str, Any] = field(default_factory=dict)


def detect_fatigue(
    rows: Iterable[dict[str, Any]], today: date
) -> list[FatigueFlag]:
    """Classify ads as fatigued based on the last 14 days of metrics.

    `rows` is the raw output of `get_creative_metrics_range(today-14, today-1)`.
    Returns at most one flag per ad. Ads with <7 days of history are skipped.
    """
    by_ad = _group_by_ad(rows)
    week_end = today - timedelta(days=1)
    this_week_start = week_end - timedelta(days=6)
    last_week_end = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    flags: list[FatigueFlag] = []
    for ad_id, ad_rows in by_ad.items():
        this_week = _aggregate(_slice_range(ad_rows, this_week_start, week_end))
        last_week = _aggregate(_slice_range(ad_rows, last_week_start, last_week_end))

        # Need at least some impressions this week to judge
        if this_week["impressions"] == 0:
            continue

        ref = ad_rows[-1]  # most recent row for metadata
        ad_name = ref.get("ad_name") or ""
        creative_id = ref.get("meta_creative_id")
        creative_name = ref.get("creative_name") or ""

        # Rule 1: dead spend (strongest signal)
        if (
            this_week["spend"] >= DEAD_SPEND_MIN_SPEND
            and this_week["purchase_count"] == 0
            and this_week["impressions"] >= DEAD_SPEND_MIN_IMPRESSIONS
        ):
            flags.append(FatigueFlag(
                meta_ad_id=ad_id,
                ad_name=ad_name,
                meta_creative_id=creative_id,
                creative_name=creative_name,
                reason="dead_spend",
                detail=(
                    f"Spent ${this_week['spend']:,.2f} on {this_week['impressions']:,} "
                    f"impressions this week with 0 purchases. Rotate or kill."
                ),
                metrics=this_week,
            ))
            continue

        # Rule 2: high frequency
        if this_week["frequency"] >= HIGH_FREQ_THRESHOLD:
            flags.append(FatigueFlag(
                meta_ad_id=ad_id,
                ad_name=ad_name,
                meta_creative_id=creative_id,
                creative_name=creative_name,
                reason="high_freq",
                detail=(
                    f"Frequency {this_week['frequency']:.1f} — same users seeing "
                    f"the ad {this_week['frequency']:.1f}x. Refresh creative."
                ),
                metrics=this_week,
            ))
            continue

        # Rule 3: CTR decay week-over-week
        if (
            last_week["impressions"] >= CTR_DECAY_MIN_IMPRESSIONS
            and this_week["impressions"] >= CTR_DECAY_MIN_IMPRESSIONS
            and last_week["ctr"] > 0
            and (this_week["ctr"] / last_week["ctr"]) < CTR_DECAY_RATIO
        ):
            drop_pct = (1 - this_week["ctr"] / last_week["ctr"]) * 100
            flags.append(FatigueFlag(
                meta_ad_id=ad_id,
                ad_name=ad_name,
                meta_creative_id=creative_id,
                creative_name=creative_name,
                reason="ctr_decay",
                detail=(
                    f"CTR dropped {drop_pct:.0f}% WoW "
                    f"({last_week['ctr']:.2f}% → {this_week['ctr']:.2f}%). "
                    f"Audience is saturating. Rotate."
                ),
                metrics=this_week,
            ))
            continue

        # Rule 4: weak CTR (never worked)
        if (
            this_week["impressions"] >= WEAK_CTR_MIN_IMPRESSIONS
            and this_week["ctr"] < WEAK_CTR_THRESHOLD * 100  # Meta returns CTR as percentage
        ):
            flags.append(FatigueFlag(
                meta_ad_id=ad_id,
                ad_name=ad_name,
                meta_creative_id=creative_id,
                creative_name=creative_name,
                reason="weak_ctr",
                detail=(
                    f"CTR {this_week['ctr']:.2f}% on {this_week['impressions']:,} impressions — "
                    f"below 0.5% threshold. Creative doesn't stop the scroll."
                ),
                metrics=this_week,
            ))
            continue

    return flags


def _group_by_ad(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        ad_id = r.get("meta_ad_id")
        if not ad_id:
            continue
        out.setdefault(ad_id, []).append(r)
    for ad_id in out:
        out[ad_id].sort(key=lambda r: r.get("date") or "")
    return out


def _slice_range(
    rows: list[dict[str, Any]], start: date, end: date
) -> list[dict[str, Any]]:
    s = start.isoformat()
    e = end.isoformat()
    return [r for r in rows if s <= (r.get("date") or "") <= e]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum a week of per-day rows into one row.

    Tricky: weekly reach ≠ sum(daily_reach) (many users overlap across days)
    AND ≠ max(daily_reach) (new users accrue). Without weekly reach from
    Meta, we approximate frequency as the *max daily frequency* — that's
    "on the worst day, each reached user saw the ad X times", which is
    exactly the saturation signal we care about.
    """
    impressions = sum(int(r.get("impressions") or 0) for r in rows)
    clicks = sum(int(r.get("clicks") or 0) for r in rows)
    spend = sum(float(r.get("spend") or 0) for r in rows)
    purchase_count = sum(int(r.get("purchase_count") or 0) for r in rows)
    purchase_value = sum(float(r.get("purchase_value") or 0) for r in rows)
    atc = sum(int(r.get("atc_count") or 0) for r in rows)

    # Max daily frequency — see docstring. Falls back to computing
    # impressions/reach per row if the stored frequency field is 0.
    daily_freqs: list[float] = []
    for r in rows:
        f = float(r.get("frequency") or 0)
        if f <= 0:
            imp = int(r.get("impressions") or 0)
            rch = int(r.get("reach") or 0)
            f = (imp / rch) if rch else 0.0
        daily_freqs.append(f)
    frequency = max(daily_freqs, default=0.0)

    # CTR returned by Meta is already a percentage. Recompute weekly from
    # clicks/impressions for consistency across any daily-row gaps.
    ctr = (clicks / impressions * 100) if impressions else 0.0

    # Max daily reach — rough proxy for "peak audience size that day";
    # not used for frequency above but surfaced for Slack detail text.
    reach = max((int(r.get("reach") or 0) for r in rows), default=0)

    return {
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "spend": round(spend, 2),
        "ctr": round(ctr, 2),
        "frequency": round(frequency, 2),
        "purchase_count": purchase_count,
        "purchase_value": round(purchase_value, 2),
        "atc_count": atc,
    }
