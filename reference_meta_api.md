---
name: Meta Marketing API quirks and gotchas
description: Non-obvious v25.0 API requirements and hidden error fields that aren't in the docs
type: reference
---

## Error extraction (the single most useful lesson)

**Meta puts the actual error in `error_user_title` + `error_user_msg`, NOT in `error.message`.**

The `message` field is almost always a useless generic: `"Invalid parameter"`, `"Unsupported request"`, `"Cannot create"`. Grabbing only `message` (which is what most first-pass error handlers do) loses the actual actionable detail.

```python
# Real example from 2026-04-05 smoke test:
{
  "error": {
    "message": "Invalid parameter",                       # useless
    "error_user_title": "No Payment Method",              # the real issue
    "error_user_msg": "Update payment method: Visit...",  # the fix
    "code": 100,
    "error_subcode": 1359188
  }
}
```

Use `src.marketing.meta_creative._extract_meta_error()` — prefers `error_user_title: error_user_msg`, falls back to `message`. Applied to every Meta 4xx/5xx handler in the client.

## Creative ≠ Ad ≠ Ad Set ≠ Campaign

Common misconception (we shipped Phase 6.1 with this wrong): "flip creative to ACTIVE = ads are serving". **False.** Four independent objects with four independent status flags, and all four must be ACTIVE for impressions to flow:

| Object | What it is | Meta API endpoint | Status flags |
|--------|-----------|-------------------|--------------|
| **Ad Creative** | Reusable asset (image + copy + CTA) in Creative Library | `POST /act_{id}/adcreatives` | DRAFT, ACTIVE, DELETED |
| **Ad** | References a creative + attaches to ad set, serves impressions | `POST /act_{id}/ads` | ACTIVE, PAUSED, ARCHIVED, DELETED |
| **Ad Set** | Contains Ads, holds targeting + budget + optimization | `POST /act_{id}/adsets` | ACTIVE, PAUSED, ARCHIVED, DELETED |
| **Campaign** | Contains Ad Sets, holds objective + special categories | `POST /act_{id}/campaigns` | ACTIVE, PAUSED, ARCHIVED, DELETED |

Creative `status` is just internal lifecycle metadata for the Creative Library. It does NOT control impression serving. Phase 6.2 fixed this by also creating an Ad object on Go Live.

## Creative status update endpoint (`POST /{creative_id}`) oddity

The UPDATE endpoint only accepts `{ACTIVE, IN_PROCESS, WITH_ISSUES, DELETED}` — **NOT `PAUSED`**. You can CREATE with `status=PAUSED`, but you cannot UPDATE an ACTIVE creative back to PAUSED. The only way to "pause" a live creative is to DELETE it or pause the Ad that uses it. This is why our dashboard's "Pause" button only updates DB state internally and doesn't call Meta.

## v25.0 required fields that didn't exist in v21.0

Discovered via direct API calls during 6.2 bootstrap — none documented in the upgrade guide:

1. **Campaigns**: `is_adset_budget_sharing_enabled=false` is now required when not using campaign-level budget. Omitting it returns error_user_title="Must specify True or False in is_adset_budget_sharing_enabled field".

2. **Ad Sets**: `targeting.targeting_automation.advantage_audience` must be set to `1` or `0` explicitly. No silent default.

3. **Ad Sets with `advantage_audience=1`**: `age_max` must be `>= 65`. Cannot combine Advantage+ audience with 25-54 targeting. Error: "Maximum age is below threshold" with error_subcode 1870189.

## Ad creation requires a payment method

Meta blocks `POST /act_{id}/ads` entirely if there's no payment method on the account, **even for PAUSED Ads under a PAUSED Ad Set**. Error: `error_user_title="No Payment Method"`, error_subcode 1359188. Add card at https://business.facebook.com/billing_hub/accounts/details/?asset_id={ad_account_numeric_id}.

Creative creation (`POST /adcreatives`) does NOT require a payment method. Pre-Phase 6.2, we could push creatives with a cardless account just fine. Only Ad creation gates on billing.

## App mode is a hidden gate

The Meta App (Pinaka Marketing, ID 930736393145618) must be in Live Mode, not Development Mode, for any Marketing API write operation against real ad accounts. Development mode blocks `/adcreatives` creation with a non-obvious error. Switching to Live Mode requires Privacy Policy URL + Data Deletion URL + app icon. We did this on 2026-04-05 during 6.1 smoke testing.

## Graph API version deprecation schedule

Meta rolls version deprecations *selectively throughout the day* — v21.0 worked in the morning and failed 20 minutes later with error #2635. Currently on v25.0 as of 2026-04-05. Recommend bumping `META_GRAPH_API_VERSION` every 3 months to stay ahead.

## Always verify writes via GET

API write responses return 200 OK before Meta's real state-check runs. To truly verify a write worked, follow up with `GET /{object_id}?fields=status,effective_status` and inspect the real state. This caught two bugs during 6.2: the `published`→`live` status tracking gap (creative was ACTIVE on Meta, DB said published+paused) and the 12-digit ID truncation in our UI banner. Logs lie, GETs don't.

## How to apply

- Any new Meta API helper in this codebase MUST extract errors via `_extract_meta_error()`, not `body.error.message`
- Any new write operation MUST verify with a GET in smoke tests, not just check the POST response
- When bumping Graph API versions, re-run the full Phase 6.1/6.2 smoke test (creative → ad) because field requirements change silently between versions
