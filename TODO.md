# TODO — Pinaka Agents

Last updated: 2026-04-17

## Phases 1-8.4: COMPLETE

All core infrastructure + agentic layer + product pipeline shipped and deployed. 243 tests passing. System is live on Railway with 5 autonomous agents, heartbeat awareness, marketing strategy crons, product pipeline dashboard, homepage video hero, and concierge chat using Shopify MCP.

## Phase 9-13: SHIPPED

- **Phase 9 (measurement + creative intel + lifecycle + retention)** — DONE
- **Phase 10 (customer intelligence: RFM, VOC, unified profile)** — DONE 2026-04-16
- **Phase 11 (bidirectional Shopify↔Supabase sync)** — DONE 2026-04-16
- **Phase 11.5 (Meta ad + Shopify blog reverse-sync)** — DONE 2026-04-17 (route fix landed same day)
- **Phase 12 (agent ownership: tiered approval, KPIs, dashboards, retros, feedback loop)** — DONE 2026-04-17
- **Phase 13 (agent intelligence)** — DONE 2026-04-17, 514 tests passing
  - Prereqs A–E: confidence defaults to `unknown`, marketing strategy tool-returned, context per-agent slices, heartbeat reframed neutral, tool docs tightened
  - 13.2 entity memory (customer / product / seasonal wikis, Karpathy llm-wiki pattern, no vectors)
  - 13.1 program-verified outcomes (closed taxonomy, SendGrid webhook, 3 SQL verifiers)
  - 13.3 cross-model skeptic (GPT-4o-mini reviews Claude, asymmetric rubric, soft-fail-open)
  - 13.4 agent rolling self-memory (extends entity_memory to agents themselves)

## Phase 12-13 follow-ups (v2 work that builds on what's shipped)

- [ ] Wire `capture_edit()` into Slack modal submissions — today, edits in Slack aren't captured. Need a view_submission handler that stores `{original, edited}` to approval_feedback. Low volume today makes this nice-to-have, but it's the input that powers 12.5's learning loop.
- [ ] Inject `founder_style_for(agent, trigger)` into ContextAssembler prompts. Scaffolded but not wired — once 10+ edits accumulate per trigger and the Sunday cron rolls them, agents should auto-use the style guidance.
- [ ] Promote/demote AUTO actions based on 30d flag_rate. Add a cron that reviews `auto_flag_rate_30d()` and either logs "this should go back to REVIEW" (>10% flag) or "this REVIEW action is ready for AUTO" (<5% edits when captured).
- [ ] Fix backfilled Shopify products failing Pydantic Product schema at startup (spams logs). Either: make Product schema fields optional for Shopify-sourced rows, or skip ChromaDB embedding for rows without full metadata.
- [ ] Clean up root directory (`.gitignore` for catalog/stories, freepik-tests, *.mp4 at root, supabase/.temp).
- [ ] **Phase 13.1 follow-up**: wire `custom_args={agent_name, action_type, audit_log_id}` into every EmailSender.send() call so SendGrid events can correlate back to the specific agent run that caused the email. Infra is ready; writers aren't attached yet.
- [ ] **Phase 13.1 follow-up**: configure SendGrid Event Webhook to POST to `https://pinaka-agents-production-198b5.up.railway.app/webhook/sendgrid`. Operator step in SendGrid admin.
- [ ] **Phase 13.3 follow-up**: harden `/webhook/sendgrid` with SG's HMAC signature verification (X-Twilio-Email-Event-Webhook-Signature) — currently accepted unsigned (URL obscurity only).
- [ ] **Phase 13.3 follow-up**: monitor skeptic `block_override_rate_pct` on `/dashboard/skeptic` — if >25% after ~20 blocks, tune the SYSTEM_PROMPT rubric or raise the confidence gate before calling it.
- [ ] **Phase 13.4 follow-up**: consider wiring `get_my_memory` into ContextAssembler as an optional automatic inject for agents with > N runs in the past week — currently strictly opt-in via tool call.

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

## Phase 9.0: Measurement Foundation — DONE (2026-04-16)

- [x] `post_purchase_attribution` migration applied to Supabase
- [x] `POST /api/attribution/submit` endpoint + order validation + observation writer
- [x] Shopify thank-you page survey widget (`shopify-theme/order-status-additional-scripts.html`)
- [x] `POST /cron/attribution-synthesize` + AttributionSynthesizer (Claude-clustered free-text)
- [x] Cron-job.org entry created (Mon 9:30 AM ET, jobId 7494627)
- [x] CAPI enriched: ViewContent/AddToCart/InitiateCheckout helpers + `/api/pixel/event` relay + Purchase event enrichment (fbp/fbc/num_items/order_id/source_url)
- [x] MER metric + MERResult dataclass; weekly Slack report leads with MER
- [x] Microsoft Clarity script in theme.liquid (conditional on new `clarity_project_id` setting) — theme pushed live
- [x] Marketing Agent prompt rewritten: measurement-first, retargeting-heavy (47/40/13), ATC + 28d defaults
- [x] 37 new tests (263 → 300 passing)

**Pending human (3 clicks):**
- [ ] **Paste Shopify thank-you survey widget** — Shopify admin → Settings → Checkout → "Order status page" → Additional scripts. Copy from `shopify-theme/order-status-additional-scripts.html`. No API exists on Basic plan.
- [ ] **Sign up Microsoft Clarity** (free) — https://clarity.microsoft.com → create project → paste ID via Theme customize → Analytics → Clarity project ID.
- [ ] **Decide on ATC ad set switch** — Meta blocks optimization edits on published ad sets. Current `Pinaka — US Purchase — Auto` optimizes for PURCHASE (stuck in learning at 1-2 orders/week). To switch: create new ATC ad set + pause old (doubles spend during overlap). Call when ready.

### High Priority (blockers / revenue)
- [x] ~~Add Meta Ad Account payment method~~ — done 2026-04-12
- [x] ~~Flip Meta Campaign + Ad Set to ACTIVE~~ — done 2026-04-12, paused for review
- [x] ~~Full checkout flow test~~ — done 2026-04-13, webhook → agent → audit all verified
- [x] ~~Meta CAPI token refresh~~ — regenerated 2026-04-13
- [x] ~~Meta Ads audit~~ — fixed spend cap, destination type, interest targeting, pixel linking, Variant B URL. Done 2026-04-13.
- [x] ~~Crafting update cron fix~~ — removed broken Claude draft, bounded query window, test orders cancelled. Done 2026-04-16.
- [x] ~~Remove "Free Lifetime Care"~~ — stripped from all modules, tests, theme. Done 2026-04-16.
- [ ] **Verify `pinakajewellery.com` domain in Meta Business Settings** — https://business.facebook.com/settings/owned-domains → add domain → add DNS TXT record in Cloudflare → verify
- [ ] **Set per-size pricing on pipeline products** — products from pipeline have $0 prices
- [ ] **More products from catalog** — remaining bracelets need Pomelli photos → upload → publish
- [ ] **Unpause Meta Campaign when ready** — Campaign ID: 120244523278190359. Ads A + B ready, all fixes applied.
- [ ] **Evaluate iAugment virtual try-on app** — https://apps.shopify.com/iaugment-virtual-try-on (free tier, 100 try-ons, bracelet support). AI-based try-on was abandoned — not realistic enough.
- [ ] **Product photography** — studio shots on cream linen with directional light

### Phase 9.2 — Lifecycle Orchestration — DONE (2026-04-16)
- [x] `customer_anniversaries` table + `customers.lifecycle_emails_sent` JSONB + `customers.welcome_started_at/welcome_step`
- [x] `post_purchase_attribution.anniversary_date/relationship` columns
- [x] Thank-you survey extension: optional "special date" field when purchase_reason is anniversary/engagement/milestone (v9 deployed)
- [x] `/api/attribution/submit`: accepts + validates anniversary + writes to customer_anniversaries
- [x] Post-purchase lifecycle orchestrator (4 triggers: care_guide_day10, referral_day60, custom_inquiry_day180, anniversary_year1)
- [x] `/cron/lifecycle-daily` (10 AM ET, jobId 7494947) + Slack approval flow (approve/skip handlers)
- [x] Welcome educational series (5 emails, day 0/3/7/12/18) + `/cron/welcome-daily` (11 AM ET, jobId 7494948)
- [x] Welcome entry on `customers/create` webhook (accepts_marketing=true + no purchases)
- [x] 6 SendGrid dynamic templates created via v3 API (`pinaka_lifecycle` + `pinaka_welcome_1..5`) — all 6 IDs set on Railway
- [x] 21 new tests (329 → 350 passing)
- **Cut from original:** browse-abandonment (needs JS tracker infra), cart-abandon enhancement (already functional).

### Phase 9.1 — Creative Intelligence — DONE (2026-04-16)
- [x] `ad_creative_metrics` table + indexes
- [x] Meta Insights per-ad daily pull (`MetaAdsClient.get_creative_insights`)
- [x] `/cron/sync-creative-metrics` (daily 7 AM ET, jobId 7494767)
- [x] Creative fatigue detector (4 rules: dead_spend, high_freq, ctr_decay, weak_ctr)
- [x] `/cron/creative-health` (daily 9:15 AM ET, jobId 7494768) — Slack alerts per fatigued ad
- [x] UGC brief generator + `/cron/ugc-brief` (Sunday 6 PM ET, jobId 7494769)
- [x] Weekly competitor brief via Claude WebSearch + `/cron/competitor-brief` (Monday 10 AM ET, jobId 7494770)
- [x] Per-creative breakdown appended to weekly ROAS Slack report
- [x] 29 new tests (300 → 329 passing)
- **Note:** original plan had "daily Meta Ad Library scraper" — pivoted to weekly Claude+WebSearch. Meta Ad Library has no public commercial API; HTML scraping is fragile/blocked. Weekly synthesis is the honest scope at our budget.
- **Note:** original plan had "auto-draft replacement on fatigue" — decided to keep founder-in-the-loop (Slack alert → manually generate at /dashboard/ad-creatives). Auto-draft noise-amplifies at our 2-creative volume.

### Phase 9.2 — Lifecycle Orchestration (after 9.1)
- [ ] Welcome-educational flow (5 emails, no discount — 4Cs + atelier story)
- [ ] Browse-abandon flow (2h delay, "still thinking?" + 4Cs PDF)
- [ ] Cart-abandon flow (1h delay, "call Jaitul" CTA at $4.9K)
- [ ] Post-purchase 6-email arc (shipping → unboxing → care → anniversary → referral → custom inquiry)
- [ ] Anniversary capture at checkout + year-out trigger
- [ ] All on SendGrid (skip Klaviyo until volume justifies $45+/mo)

### Phase 10 — Customer Intelligence Layer — DONE (2026-04-16)
- [x] `customer_rfm` + `customer_insights` tables + `customers.last_rfm_at/last_segment`
- [x] Unified customer profile (`src/customer/profile.py`) + `GET /api/customer/{id}/profile` (dashboard-auth)
- [x] RFM scorer with 7-segment ladder (champion/loyal/at_risk/new/hibernating/one_and_done/active) + LTV projection. `/cron/rfm-compute` daily 8AM ET (jobId 7495377).
- [x] Voice-of-customer weekly theme miner (`src/customer/voc.py`) clustering emails+chats+surveys. `/cron/voc-mine` Mon 11AM ET (jobId 7495378).
- [x] Review request automation — 5th lifecycle trigger `review_request_day20` (~5d post-delivery).
- [x] Closed-loop Meta insights → ad creative generation: top-performers feed into Claude's prompt.
- [x] Dashboard brief upgrade: segment distribution pills + VOC themes surfaced on /dashboard/brief.
- [x] 30 new tests (379 → 409 passing).

**Pending human setup for Phase 10:**
- [ ] Replace `GOOGLE_REVIEW_URL` placeholder in `src/customer/lifecycle.py` with your actual Google Business Profile review URL.
- [ ] Verify Trustpilot URL in lifecycle.py matches your actual profile (create account if needed).

### Phase 9.3 — Content & Retention Engine — DONE (2026-04-16)
- [x] Daily AI brief at `/dashboard/brief` — password-protected, aggregates MER + creatives + observations + seasonal + pending queues + SEO. Claude writes 3-paragraph "focus today" narrative.
- [x] `seo_topics` table + 25-keyword long-tail rotation (anniversary/comparison/education/occasion).
- [x] Weekly SEO journal writer — Claude drafts 900-1,400 word post + title/meta/slug/tags. `/cron/seo-post` Mon 2 PM ET (jobId 7494975).
- [x] Auto-publish to Shopify blog as DRAFT (requires `write_content` scope — fallback to Slack-paste if not yet granted).
- [x] Piece of the Quarter quarterly email — Claude drafts, Slack approves, SendGrid batch-sends to past buyers. `/cron/quarterly-poq` 1st Mon Jan/Apr/Jul/Oct (jobId 7494977).
- [x] Pinterest Tag (conversion pixel) — `pinterest_tag_id` theme setting + theme.liquid script.
- [x] Pinterest API v5 posting client + `/cron/pinterest-pins` Mon/Wed/Fri 1 PM ET (jobId 7494976). No-ops without token.
- [x] 29 new tests (350 → 379)

**Pending human setup for Phase 9.3:**
- [ ] Re-install Shopify app to grant `write_content` scope (enables SEO auto-publish). Without it, SEO posts fall back to Slack-paste mode (still works).
- [ ] After scope re-auth, run `railway run python scripts/setup_shopify_blog.py` to discover + set `SHOPIFY_BLOG_ID`.
- [ ] Create Pinterest Business account → dev app at developers.pinterest.com → generate access token with `pins:write` + `boards:read` → paste PINTEREST_ACCESS_TOKEN + PINTEREST_BOARD_ID on Railway.
- [ ] Install Pinterest Tag — ads.pinterest.com → Conversions → get tag ID → paste in Theme customize → Analytics → Pinterest Tag ID. Separate from API posting.

### Medium Priority (improvements)
- [ ] Google Ads developer token approval (in review since 2026-04-04)
- [ ] Google Ads OAuth2 setup (after token approved)
- [ ] Review request automation (post-delivery email, 7-14 days after delivery)
- [ ] Slide-in cart drawer for mobile
- [ ] Closed-loop Meta insights feedback into ad creative generation
- [ ] 3-campaign structure (Prospecting/Retargeting/Retention) — currently single campaign
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
