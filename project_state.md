---
name: Pinaka Agents — Phase 6.2 complete, pending Meta payment method
description: Project phase tracking and deployment state as of 2026-04-05
type: project
---

Phases 1-6.2 complete and deployed on Railway as of 2026-04-05. 197 tests passing.

**Production URL:** https://pinaka-agents-production-198b5.up.railway.app
**Cron management:** cron-job.org (API key stored, 11 active jobs)
**Shopify webhooks:** 4 registered (orders/create, checkouts/create, customers/create, refunds/create)

**Phase 6.1 — Automated Ad Creative Generation:** LIVE. Claude Sonnet 4 generates 3 variants per product with brand-DNA validation + prompt injection defense, dashboard review at `/dashboard/ad-creatives`, Meta Ad Creative API push with status=PAUSED soft-pause window, atomic approve transition. Smoke tested 2026-04-05 with real Claude + real Meta push. 2 creatives live in Meta Creative Library (`959138700395572`, `1679259843246920`).

**Phase 6.2 — Auto-create Meta Ad on Go Live:** CODE COMPLETE, BLOCKED ON PAYMENT METHOD. Dashboard Go Live button creates a Meta Ad object under the default Ad Set automatically. Campaign `120244523278190359` + Ad Set `120244523287540359` bootstrapped via API ($25/day, US 25-65, purchase-optimized, both PAUSED). Meta blocks Ad creation entirely without a payment method on the ad account — must add card at https://business.facebook.com/billing_hub/accounts/details/?asset_id=27080581041558231 before Phase 6.2 can be fully exercised.

**Meta Marketing:** Never-expiring System User token (no 60-day renewal). Graph API v25.0 (bumped from v21.0 on 2026-04-05 mid-deprecation). App in Live Mode.

**Anthropic API:** Separate from Claude.ai Pro/Max plan. Billed at console.anthropic.com, $5 credited during 6.1 session unblocks ~250 ad generation batches (~$0.02 each).

**Google track:** Code complete but waiting on Developer Token approval (applied 2026-04-04, Basic Access pending 2-15 day review).

**Why:** Building toward fully autonomous e-commerce ops. Phases 6.1 + 6.2 give founder a draft-to-impressions pipeline with Claude-generated copy and a single Go Live click (after first-time Ad Set activation).

**How to apply:** When user asks about next steps, refer to TODO.md. When working on Meta integrations, always check reference_meta_api.md for quirks (error extraction, v25.0 field requirements, payment method gates).
