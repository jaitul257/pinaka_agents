# TODO — Pinaka Agents

Last updated: 2026-04-05

## Phases 1-5: COMPLETE

All core infrastructure shipped and deployed. 126 tests passing. System is live on Railway.

---

## Pending: External Platform Setup (no code needed)

### Google Ads API (blocks Google ad spend sync + offline conversions)
- [x] Applied for Google Ads Developer Token (2026-04-04, Manager 708-325-3807, Token: V6l4c0c4rIoZxMOeFSl72Q, currently Test Account — awaiting Basic Access approval, 2-15 business days)
- [ ] Link Google Ads regular account 268-380-3995 as sub-account under Manager 708-325-3807
- [ ] Create OAuth2 credentials in Google Cloud Console
- [ ] Generate refresh token via google-ads auth helper
- [ ] Set Railway env vars: `GOOGLE_ADS_CUSTOMER_ID=2683803995`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID=7083253807`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`

### Google Merchant Center (blocks Google Shopping ads)
- [x] Created Merchant Center account 5759598456 (2026-04-04, under jaitul25@gmail.com, connected via Shopify Google & YouTube app)
- [x] Verified + claimed pinakajewellery.com custom domain
- [x] Configured shipping (15 day handling + 2-5 day transit, free insured) and returns policy
- [x] First product synced (Diamond Tennis Bracelet - Lab Grown) with age_group/gender/color/mpn metafields
- [x] Old duplicate Merchant Center 5757278712 superseded (can delete later, low priority)
- [ ] Create service account in Google Cloud Console (for API-based sync, currently using Shopify native app)
- [ ] Set Railway env vars: `GOOGLE_SERVICE_ACCOUNT_PATH` (already set: `GOOGLE_MERCHANT_ID=5759598456`)

### Google Ads Conversion Action (blocks server-side conversion tracking)
- [ ] Create conversion action in Google Ads → Tools → Conversions → Import → Upload clicks
- [ ] Set Railway: `GOOGLE_ADS_CONVERSION_ACTION_ID`

### Meta Ads Token Renewal — DONE (2026-04-04)
- [x] Generated never-expiring System User token from "Conversions API System User" (expires_at=0)
- [x] Railway env vars set: `META_ADS_ACCESS_TOKEN`, `META_BUSINESS_ID=1035697978984161`, `META_CATALOG_ID=2850427255291757`, `META_APP_ID=930736393145618`, `META_AD_ACCOUNT_ID=act_27080581041558231` (corrected from wrong `act_149386420603321`)
- [x] Verified: token valid, all scopes present (ads_management, ads_read, catalog_management, business_management), ad account + catalog both reachable
- [x] `META_APP_SECRET` set on Railway (2026-04-04, verified via appsecret_proof — stored for future use, codebase does not currently require it since token is scoped + never-expiring)

---

## Phase 6: Planned Features

### 6.0 Shopify Storefront Design & Build
- [x] Design system updated for Shopify (DESIGN.md — see "Shopify Storefront Sections")
- [x] Connect custom domain pinakajewellery.com to Shopify (2026-04-04, Cloudflare DNS, primary domain, SSL live, SHOPIFY_STOREFRONT_URL env var set, ad catalog feeds updated to use custom domain)
- [ ] Apply design system to Shopify theme (hero, collection grid, PDP, footer) — theme files exist in `shopify-theme/` (untracked, mid-build)
- [ ] Product photography: studio shots on cream linen with directional light
- [ ] Implement Atelier Ledger section (live order timeline from Supabase)
- [ ] Craft Timeline section (5-step making process)
- [ ] Founder Note section
- [ ] Mobile: sticky bottom CTA on PDP, slide-in cart drawer
- **Ref:** Design preview HTML at `/tmp/design-consultation-preview-1775112210.html`. Run `/design-consultation` for full context.
- **Ref:** DESIGN.md has full specs: hero layout, collection grid, PDP buy flow, navigation, photography direction, anti-patterns.
- **Why:** Current store is default Dawn theme with zero brand identity. Design system ready, needs implementation.

### 6.1 Automated Ad Creative Generation — DONE (2026-04-05)
- [x] AI-generated ad copy per product (headline, primary_text, description, CTA) via Claude Sonnet 4
- [x] Product image selection from Shopify catalog (deterministic, 1 per variant, no duplicates)
- [x] 3-variant generation with brand-DNA validation + URL allowlist + prompt injection defense
- [x] Dashboard review at /dashboard/ad-creatives (no Slack — per founder direction)
- [x] Meta Ad Creative API push with status=PAUSED (soft-pause window, 2am-typo protection)
- [x] Atomic approve transition (race-safe via UPDATE-WHERE-status=pending_review)
- [x] Background task generation with idempotency key (sha1 sku+minute) to survive Claude 10-25s calls
- [x] Create Facebook Page for Pinaka Jewellery + link to Business Portfolio (2026-04-05, Page ID 982012465004487)
- [x] Set `META_FACEBOOK_PAGE_ID` on Railway (2026-04-05)
- [x] Run migration 007 via Supabase CLI (2026-04-05, after `migration repair`)
- [x] Switch Pinaka Marketing app (930736393145618) from Development Mode to Live Mode (2026-04-05)
- [x] Fix status tracking bug: added `live` status via migration 008, fixed ID truncation, full end-to-end verified (2026-04-05)
- [x] Fix Shopify→Supabase image sync: cron now writes `products.images`, dashboard has lazy backfill (2026-04-05)
- **Deferred to 6.1.1:** closed-loop Meta insights feedback into next prompt generation
- **Why:** Creative is the top of the funnel. Even if outside voices said it's premature at $75/day, founder directed "ship to learn" — infrastructure ready before budget scales.

### 6.2 Auto-create Meta Ad on Go Live — DONE (2026-04-05, pending payment method)
- [x] `MetaCreativeClient.create_ad()` — POSTs to `/act_{id}/ads`, full error handling with `error_user_title`/`error_user_msg` extraction
- [x] Bootstrap Campaign `120244523278190359` (OUTCOME_SALES, PAUSED) + Ad Set `120244523287540359` (US 25-65, $25/day, purchase-optimized, PAUSED) via Meta API
- [x] Railway env vars set: `META_DEFAULT_CAMPAIGN_ID`, `META_DEFAULT_ADSET_ID`
- [x] Migration 009: `meta_ad_id` + `meta_adset_id` columns on ad_creatives
- [x] Dashboard "Go Live" flow creates Ad object automatically, deep-links card to Ads Manager
- [x] Backwards-compat: falls back to creative-only mode if no default ad set configured
- [x] 197 tests green (10 new unit + 2 integration + 1 regression test)
- **PENDING HUMAN (BLOCKER):** Add payment method at https://business.facebook.com/billing_hub/accounts/details/?asset_id=27080581041558231 — Meta blocks Ad creation without a card, even for PAUSED Ads under a PAUSED Ad Set. Error surfaces as "No Payment Method: Update payment method".
- **PENDING HUMAN (one-time):** Flip default Ad Set `120244523287540359` from PAUSED → ACTIVE in Ads Manager once payment method is in place. After this, all future Go Live clicks serve impressions immediately.
- **PENDING HUMAN (one-time):** Flip default Campaign `120244523278190359` from PAUSED → ACTIVE in Ads Manager.
- **State:** 2 creatives live on Meta (`959138700395572`, `1679259843246920`), 0 Ads (blocked), 1 variant pending_review (Variant C of batch ddeea2d8).
- **Why:** Collapses the "attach creative to ad set" manual step that used to require a trip to Ads Manager after every approval. One click from draft to impressions (after first-time setup).

### 6.3 Review Request Automation
- [ ] Post-delivery review solicitation via email (7-14 days after delivery)
- [ ] Platform-specific links (Google Reviews, Trustpilot)
- [ ] Slack approval before sending
- [ ] Track review rate and sentiment
- **Why:** Social proof drives DTC conversion. Automate the ask.

---

## Operational: Ongoing Maintenance

- [x] ~~Monitor Meta Ads token expiry~~ — now a never-expiring System User token (2026-04-04)
- [ ] Monitor cron job success rates on cron-job.org dashboard
- [ ] Review Sentry for production errors weekly
- [ ] Check ROAS Slack reports weekly and act on budget recommendations
- [ ] Verify Shopify webhook health (auto-recovery runs every 30 min)

---

## Known Gaps (low priority)

- [ ] `apply_budget_change` Slack button logs action but doesn't auto-change Meta/Google budgets (intentional, deferred until API proven stable)
- [ ] `datetime.utcnow()` deprecation warnings in app.py (12 instances, cosmetic, Python 3.12+)
- [ ] Health endpoint does shallow checks only (no real DB/Shopify connectivity test)
- [ ] No CI/CD pipeline (tests run locally, Railway auto-deploys on push)
- [ ] No pytest-cov integration (coverage not measured)
- [ ] Dashboard uses sync Database (not migrated to async, Streamlit has different runtime)
