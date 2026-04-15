# TODO — Pinaka Agents

Last updated: 2026-04-10

## Phases 1-8.4: COMPLETE

All core infrastructure + agentic layer + product pipeline shipped and deployed. 243 tests passing. System is live on Railway with 5 autonomous agents, heartbeat awareness, marketing strategy crons, product pipeline dashboard, homepage video hero, and concierge chat using Shopify MCP.

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

### 6.0 Shopify Storefront Design & Build — MOSTLY DONE
- [x] Design system updated for Shopify (DESIGN.md — see "Shopify Storefront Sections")
- [x] Connect custom domain pinakajewellery.com to Shopify (2026-04-04, Cloudflare DNS, primary domain, SSL live)
- [x] Theme CSS customizations (`shopify-theme/assets/pinaka-custom.css`)
- [x] About page section + template (`shopify-theme/sections/pinaka-about.liquid`)
- [x] Sticky bottom CTA on PDP (IntersectionObserver, mobile-only)
- [x] AI concierge chat widget live on all pages
- [x] Dark mode support for all custom sections
- [x] Trust badges, Atelier Ledger section, Craft Timeline
- [ ] Product photography: studio shots on cream linen with directional light
- [ ] Slide-in cart drawer
- **Ref:** DESIGN.md has full specs.

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

## Phase 7: Storefront — DONE (2026-04-07)

- [x] Homepage sections (trust badges, atelier ledger, craft timeline)
- [x] PDP Metal/Wrist Size variants (12 combos, per-size pricing $4,500-$5,100)
- [x] Design system alignment (Cormorant Garamond/Geist Mono/DM Sans)
- [x] Dark mode, sticky CTA, chat widget

## Phase 8: Agentic Layer — DONE (2026-04-08)

- [x] Agent framework (BaseAgent, ToolRegistry, PolicyEngine, ContextAssembler, AuditLogger)
- [x] 5 specialized agents (Order Ops, Customer Service, Marketing, Finance, Retention)
- [x] Dual-path webhook (agent_enabled flag, fallback to procedural)
- [x] Confidence scoring + auto-escalate on low confidence
- [x] Cross-agent feedback loop (finance margins → marketing budget allocation)
- [x] Customer memory (past 10 interactions in context)
- [x] Token optimization (51% reduction on Order Ops)
- [x] Slack Block Kit formatting for all agent posts
- [x] Storefront AI concierge chat widget (Claude + Shopify MCP)
- [x] Heartbeat awareness system (observations table + 30-min SQL checks)
- [x] Marketing strategy: 3-campaign structure, seasonal calendar, 6h data snapshots, Monday weekly review
- [x] Abandoned cart flow fix: mark_abandoned_carts transition (2026-04-07)
- [x] 14 active cron jobs on cron-job.org

## Phase 8.4: Product Pipeline + Polish — DONE (2026-04-10)

- [x] PDF catalog extraction — 13 bracelets parsed from `BraceletsbyPinaka.pdf` with metadata
- [x] `/dashboard/pipeline` page — download base image for Pomelli, upload lifestyle shots, one-click Shopify create
- [x] Pipeline creates products with Metal × Wrist Size variant matrix (3 × 4 = 12 variants)
- [x] Carat string parser handles colored diamond format ("White 1.35CT + Blue 1.80CT")
- [x] Pipeline only uploads Pomelli lifestyle images to Shopify, not raw catalog base images
- [x] Product status Active/Draft dropdown added to edit form
- [x] `published_at` auto-set when status flips to active (Shopify requires both for storefront visibility)
- [x] Hero video on homepage (9:16 portrait, 9/16 aspect container, no cropping)
- [x] Mobile UX: dark mode toggle + chat button scroll-hide, alignment fix, full-screen chat panel
- [x] Concierge bugfix: Shopify MCP tool renamed `search_shop_catalog` → `search_catalog`
- [x] Concierge: better error logging, price parsing (cents→dollars), image extraction from media array
- [x] Freepik API integration researched and tested (Kling o1, Mystic, Flux Pro, Imagen3)
- [x] Real photographer vocabulary prompt template for AI product photography

---

## Next Up

### High Priority (blockers / revenue)
- [x] ~~Add Meta Ad Account payment method~~ — done 2026-04-12
- [x] ~~Flip Meta Campaign + Ad Set to ACTIVE~~ — done 2026-04-12, paused for review
- [x] ~~Full checkout flow test~~ — done 2026-04-13, webhook → agent → audit all verified
- [x] ~~Meta CAPI token refresh~~ — regenerated 2026-04-13, old token was invalidated
- [ ] **Verify `pinakajewellery.com` domain in Meta Business Settings** — https://business.facebook.com/settings/owned-domains → add domain → add DNS TXT record in Cloudflare → verify. Unverified domain reduces ad reach.
- [ ] **Set Meta attribution to 7-day click + 1-day view** — Ads Manager → Ad Set → Settings → Attribution. API blocked this change.
- [ ] **Set per-size pricing on pipeline products** — 7 Shopify products from pipeline have $0 prices. Edit each in dashboard, set prices, save with Push to Shopify.
- [ ] **More products from catalog** — 7 remaining bracelets need Pomelli photos → upload → publish via pipeline (AB0027, AB0025B, AB0024A, AB0029A, AB0025AB, AB0029AE, AB0025BR)
- [ ] **Unpause Meta Campaign when ready** — Campaign ID: 120244523278190359. Both ads (A + B) ready, targeting + destination + spend cap all fixed.
- [ ] **Product photography** — studio shots on cream linen with directional light

### Medium Priority (improvements)
- [ ] Google Ads developer token approval (in review since 2026-04-04)
- [ ] Google Ads OAuth2 setup (after token approved)
- [ ] Review request automation (post-delivery email, 7-14 days after delivery)
- [ ] Slide-in cart drawer for mobile
- [ ] Closed-loop Meta insights feedback into ad creative generation
- [ ] 3-campaign structure (Prospecting/Retargeting/Retention) — currently single campaign, marketing strategy calls for 3
- [ ] Additional ad creatives — Meta recommends 3-5 per ad set, currently 2

### Low Priority (tech debt)
- [ ] `apply_budget_change` Slack button: auto-change Meta/Google budgets (intentional defer)
- [ ] `datetime.utcnow()` deprecation warnings (cosmetic, Python 3.12+)
- [ ] Health endpoint: add real DB/Shopify connectivity test
- [ ] CI/CD pipeline (tests run locally, Railway auto-deploys on push)
- [ ] pytest-cov integration

---

## Operational: Ongoing Maintenance

- [x] ~~Monitor Meta Ads token expiry~~ — now a never-expiring System User token (2026-04-04)
- [ ] Monitor cron job success rates on cron-job.org dashboard
- [ ] Review Sentry for production errors weekly
- [ ] Check ROAS Slack reports weekly and act on budget recommendations
- [ ] Verify Shopify webhook health (auto-recovery runs every 30 min)
