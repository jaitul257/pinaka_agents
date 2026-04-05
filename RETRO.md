# Retrospective — Pinaka Agents

Last updated: 2026-04-04 (late evening)

## How to Use This File
- **Read this before starting any new work.** It captures what happened, what worked, what didn't, and what to do differently.
- **Update after every push to main.** Add a new entry with: what shipped, what went well, what was painful, and lessons learned.
- **Keep entries short.** 3-5 bullets per push. This is a learning log, not a changelog.

---

## Push Log

### 2026-04-05 — Phase 6.1: Automated Ad Creative Generation

**What shipped:**
- `BrandDNA` module (`src/marketing/brand_dna.py`) — formalizes tone/palette/banned-phrases from DESIGN.md + voice_examples table, with mtime-aware cache invalidation
- `AdCreativeGenerator` (`src/marketing/ad_generator.py`) — Claude Sonnet 4 generates 3 tagged variants per product with prompt-injection defense (delimiters + URL allowlist + length truncation), banned-word validation with one retry, single-image fallback
- `MetaCreativeClient` (`src/marketing/meta_creative.py`) — creates Meta Ad Creative objects with `status=PAUSED` default (soft-pause window), plus `set_creative_status()` for Go-Live / Pause flips
- Dashboard page at `/dashboard/ad-creatives` — list + review + approve/reject/go-live/pause buttons, reuses existing DESIGN.md tokens + `_base_html()` + cookie auth
- Background-task generation with idempotency key to survive Claude's 10-25s calls without blocking the request handler
- Atomic approve transition (UPDATE-WHERE-status=pending_review) — race-safe against double-clicks
- Migration `007_ad_creatives.sql` + `generation_batches` helper table
- 59 new tests (126 → 185 total, all passing): 7 brand_dna + 25 ad_generator + 15 meta_creative + 12 dashboard integration

**What went well:**
- Running `/autoplan` caught architectural issues BEFORE any code: eng subagent flagged 4 critical + 5 high issues (timeout on inline Claude call, sync-vs-async dashboard mismatch, double-push race, prompt injection via product.story). All incorporated into plan before implementation.
- Outside voices (Codex + Claude CEO subagent) independently agreed 6/6 dimensions that the premise was weak. The founder override ("ship to learn") was documented transparently in the plan — so if 6.1 turns out to be dead code in 6 months, the RETRO will have the receipts.
- 94% reuse of existing patterns (listings/generator.py for Claude, listing_drafts schema for ad_creatives, dashboard `_base_html` for UI). Zero new infrastructure concepts.
- Dashboard integration tests used MagicMock patched at `src.dashboard.web._get_db` — clean, fast, no real DB/Claude/Meta calls.
- Test-first approach exposed edge cases: single-image fallback, URL allowlist catching evil.com in prompt-injection output, atomic transition returning None on race.

**What was painful:**
- Broken venv shebang (`/Users/jaitulbharodiya/Documents/pinaka_agents/.venv/bin/python3.12` — wrong path because repo was moved). Workaround: `.venv/bin/python3 -m pytest` directly. Worth fixing the venv but not urgent.
- Eng subagent output came back as JSONL via background agent — had to extract final text via Python parser, not a simple `tail`.
- Bun not on PATH (`~/.bun/bin/bun`). Gstack browse skill failed on first invocation. Worked around with explicit `export PATH`.
- First /autoplan CEO review returned a STRONG "kill this plan" verdict. Had to trust the founder's override and document the tradeoff explicitly instead of steamrolling either direction.

**Lessons learned:**
- **When both outside voices independently converge on "you're solving the wrong problem," treat it as a rare high-confidence signal.** The right response isn't always "change course" — sometimes the user has context the models don't — but you MUST surface the disagreement transparently, not bury it. The plan file now has the full Round 1/Round 2 decision history so we can look back in 6 months and learn.
- **"Background task + idempotency key" is the right pattern for any inline AI call in a request handler.** 10-25s Claude calls hit Railway proxy timeouts. The eng subagent caught this; the architecture diagram would have hidden it.
- **Atomic UPDATE-WHERE-status=X is the simplest race condition fix.** No locks, no Redis, no queue — just let the database refuse the second transition. Returns None to caller who shows "already processed".
- **`status=PAUSED` on first Meta push is the single best safeguard against approval fatigue.** Founder clicks approve at 2am half-asleep, types a typo, approves... but the creative sits in Meta Ads Manager paused. No money burned until a second conscious click.
- **Prompt injection defense needs FOUR layers:** (1) delimiters around user fields, (2) length truncation before prompt, (3) URL allowlist on OUTPUT, (4) banned-word check on OUTPUT. Any single layer can be bypassed; all four together make it hard.
- **BrandDNA mtime-aware caching** is worth the 3 lines of code — prevents stale cache from surviving a DESIGN.md edit during a session.
- **The eng subagent's "2am Friday" scenario was the most valuable finding.** It's not a real bug but it's a design philosophy — every "approve and go live" action should have an undo window. Soft-pause + Go-Live button is that undo window.

**Pending human steps (must complete before first real Meta push):**
1. ~~Create Facebook Page for Pinaka Jewellery in Meta Business Suite~~ DONE 2026-04-05 (Page ID 982012465004487)
2. ~~Link Page to Pinaka Jewellery Business Portfolio (1035697978984161)~~ DONE 2026-04-05
3. ~~Set `META_FACEBOOK_PAGE_ID` env var on Railway~~ DONE 2026-04-05
4. **Switch Pinaka Marketing app (930736393145618) from Development Mode to Live Mode** — discovered via live smoke test after page setup. Meta blocks ad creative creation from Development-mode apps. Requires Privacy Policy URL, Data Deletion URL, and app icon. 5-10 min fix at https://developers.facebook.com/apps/930736393145618/app-review/status/
5. Run migration 007 via Supabase Dashboard SQL Editor (CLI not linked locally)

**New lesson from smoke test:**
- **"Live Mode" is a hidden gate for ANY Meta Marketing API write operation.** The eng subagent correctly flagged FB Page as a blocker, but nobody flagged app-mode. It only surfaces when you actually try to POST to `/adcreatives`. Future Meta integrations should verify app-mode as part of the readiness check — add a preflight ping to `/me?fields=is_test_user` or try a safe write to surface app-mode issues before the first real call.
- **Code handled the failure correctly.** `MetaCreativeClient` raised `MetaCreativeError` with Meta's full error body, `ad_creatives_approve` rolled back the atomic transition via `revert_ad_creative_to_pending`, draft returned to `pending_review`. No money burned, no data lost, no manual cleanup needed. This is exactly what the eng subagent's "ship to learn, not to lose money at 2am" recommendation was designed to prevent.

---

### 2026-04-04 (late evening) — Meta Ads Never-Expiring System User Token

**What shipped:**
- Generated a never-expiring System User token from the "Conversions API System User" in Pinaka Jewellery Business Portfolio (`1035697978984161`) — no more 60-day renewal cycles
- Railway env vars set/updated: `META_ADS_ACCESS_TOKEN` (new token), `META_BUSINESS_ID=1035697978984161`, `META_CATALOG_ID=2850427255291757`, `META_APP_ID=930736393145618`
- **Discovered and corrected wrong `META_AD_ACCOUNT_ID`** on Railway: was `act_149386420603321` (stale/unreachable), actual account linked to the System User is `act_27080581041558231` ("Pinaka Jewellery's ad account", USD, America/Los_Angeles, $0 spend)
- Verified token end-to-end: valid, `expires_at=0` (NEVER), all required scopes (`ads_management`, `ads_read`, `catalog_management`, `business_management`, `attribution_read`), ad account + catalog + insights endpoints all return 200

**What went well:**
- Debug-token endpoint (`/debug_token?input_token=...`) is the fastest way to verify type, expiry, and scopes in one call — should be the first check on any new Meta token
- Once the System User had the right app role, token generation was one click

**What was painful:**
- **Wrong ad account ID in Railway all along.** Token looked broken (`Ad account owner has NOT granted ads_management or ads_read permission`), but the root cause was the ID itself — the account in Railway wasn't the account the System User had access to. Spent time chasing permissions before checking the ID. Lesson: when Meta says "no permission", also suspect "wrong resource ID", not just scopes.
- First token attempt failed with "No permissions available" because the System User had no app role on Pinaka Marketing. Required adding the app to the System User inside Business Settings → System Users → Assets.
- Meta's UI doesn't show which ad account a System User is actually linked to without calling `/me/adaccounts` — had to discover the correct ID via API.

**Lessons learned:**
- **Always use System User tokens for server-to-server Meta integrations.** User access tokens (even long-lived) expire every 60 days; System User tokens issued with `set_token_expires_in_days=0` never expire. No renewal cron needed.
- **When a Meta API call returns a permission error, verify the resource ID before re-granting scopes.** The error "ad account owner has NOT granted X permission" can mean the token has no access to *this specific* account — which is also true when the account ID is simply wrong or stale.
- **Use `GET /me/adaccounts?access_token=...` to discover which ad accounts a System User can actually reach.** This is the ground truth; don't trust env vars from six months ago.
- **System User ≠ app role automatically.** Even if the System User exists in the Business Portfolio, you must explicitly assign the app (Pinaka Marketing) in Business Settings → System Users → Add Assets → Apps, or token generation returns "No permissions available".
- **Business Portfolio resources have their own catalog.** The Conversions API System User's catalog (`2850427255291757`, "Shopify Product Catalog System User") is distinct from any catalog tied to an individual user account. Make sure Railway's `META_CATALOG_ID` matches the one the token can actually reach.

---

### 2026-04-04 (evening) — Custom Domain + Google Ads Setup

**What shipped:**
- Custom domain `pinakajewellery.com` connected to Shopify via Cloudflare DNS (DNS-only mode, primary domain, SSL live)
- New `shopify_storefront_url` setting + `storefront_domain` property; Meta/Google catalog feeds now use the custom domain for customer-facing product links (Admin API still uses myshopify)
- Auto `age_group` / `gender` / `color` metafields added to `_upsert_google_metafields` — jewelry products in Google's Apparel category require these for full visibility
- Google Ads Developer Token applied for Basic Access (Manager `708-325-3807`, token `V6l4c0c4rIoZxMOeFSl72Q`, awaiting 2-15 day review)
- Linked regular Ads account `268-380-3995` as sub-account under Manager `708-325-3807`
- Railway env vars consolidated: `SHOPIFY_STOREFRONT_URL`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID`, `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_MERCHANT_ID=5759598456`

**What went well:**
- Every Google Merchant Center issue (domain mismatch, missing MPN, missing age_group/gender/color) was fixable by extending one helper (`_upsert_google_metafields`) and re-saving the product from the dashboard — the infrastructure we built earlier paid off immediately
- Cloudflare DNS setup was smooth once the grey cloud (DNS only) rule was understood
- Writing a proper Google Ads API design doc upfront (instead of hoping for automated approval) matches what Google's reviewers actually want

**What was painful:**
- **Account email chaos**: Merchant Center ended up under `jaitul25@gmail.com`, Google Ads Manager under `jaitul257@gmail.com`. Spent significant time untangling which account owned what. Having two Gmail accounts for the same business is a trap — everything should be under one.
- Cloudflare + Shopify "Error 1000 DNS points to prohibited IP" — the www subdomain was proxied (orange cloud) when it needed to be DNS-only (grey cloud). Cached response took a hard refresh to clear.
- Two Merchant Center accounts existed (`5757278712` old/abandoned, `5759598456` new/active) because of prior setup attempts. Had to reconcile which one was "real".
- Got confused myself about whether developer tokens require Manager (MCC) accounts — initially told the user "no Manager needed", was wrong. Developer tokens ONLY come from Manager accounts. Corrected mid-conversation. Need to remember this for next time.
- Shopify Google & YouTube app pushed the product with the old myshopify URL initially — "Mismatched domains" error. Fix: set primary domain first, then re-save the product to trigger re-sync.

**Lessons learned:**
- **Developer tokens require Manager (MCC) accounts.** Regular Google Ads accounts don't have API Center. This is non-negotiable Google policy.
- **Cloudflare + Shopify = DNS only mode (grey cloud), always.** Orange cloud causes SSL errors, Error 1000, and infinite redirects. Shopify handles SSL + CDN via Fastly; Cloudflare proxy just gets in the way.
- **Save the "why this took two emails" pain to memory.** Future setups: ONE Google account per business, no exceptions. Use a dedicated Gmail or Workspace email tied to the brand.
- **Google Merchant Center requires both product-level metafields AND category-level requirements.** For jewelry (Apparel & Accessories): mpn, condition, custom_product, google_product_category, age_group, gender, color. Missing any → "Limits visibility" warning.
- **Shopify primary domain at time of product sync = the URL that ends up in Google's feed.** Change primary domain later → stale feeds until a product update triggers re-sync. Build the custom domain before running the initial product sync.
- **Writing a proper API design doc for Google Ads Basic Access review gets faster approval than a one-line justification.** Include architecture, data flow, rate limits, security, and explicit "first-party, single account, no SaaS" language.
- **Trust the automated-but-stale UI banners, verify via the source-of-truth page.** Merchant Center showed "Verify website" banner even after domain was verified — Business Info page showed "Verified + Claimed". Always check the actual settings page before debugging cached notifications.

---

### 2026-04-04 — Persistent Storage Fix & Streamlit Removal

**What shipped:** Eliminated all ephemeral local file storage. Products now persist across Railway deploys via Supabase. ChromaDB rebuilds on startup. Removed dead Streamlit dashboard (1,122 lines). Fixed 2 broken reconcile tests. Fixed Settings `extra` config. 126/126 tests passing.

**What went well:**
- Thorough audit before coding — traced every data flow (Shopify ↔ Dashboard ↔ Supabase ↔ ChromaDB) which revealed the Streamlit dashboard was dead code
- The HTML dashboard (`web.py`) was already wired correctly to Supabase+Shopify, so no dashboard rewrite needed
- ChromaDB startup rebuild worked first try — 2 products embedded in ~5 seconds on deploy

**What was painful:**
- Tests wouldn't run locally due to Railway CLI injecting `RAILWAY_*` env vars that Pydantic Settings rejected (`extra = "forbid"` by default). Pre-existing bug, never caught because tests were presumably run before Railway CLI was linked.
- Two reconcile tests were broken for the same reason documented in CLAUDE.md: using `MagicMock` instead of `AsyncMock` for async Slack methods. Pattern keeps repeating.

**Lessons learned:**
- Always audit before implementing. The "fix Streamlit dashboard" task turned into "delete Streamlit dashboard" once we checked what's actually deployed.
- Two dashboards existed doing the same thing differently — one saved to local JSON, one to Supabase. Duplication breeds inconsistency. Single source of truth matters.
- Pydantic BaseSettings defaults to `extra = "forbid"`. Any environment (Railway, Docker, CI) that injects extra env vars will break it. Always set `extra = "ignore"`.
- ChromaDB downloads its ONNX model (~79MB) on every Railway deploy since the container resets. Adds ~2s to startup. Could cache via Railway volume if it becomes a problem.
- When a test needs to mock an async method, **always use AsyncMock**. This is the third time this lesson appears — it should be muscle memory by now.
- `embed_all_from_directory()` was defined but never called anywhere — dead code. Always grep for callers before assuming a function is used.

---

### 2026-04-02 — Phase 6.0 Design System (Shopify Storefront)
**What shipped:** Updated DESIGN.md with complete Shopify storefront design system (hero, collection, PDP, navigation, mobile patterns, photography direction, Atelier Ledger, anti-patterns). TODO.md updated with Phase 6.0 tasks.

**What went well:**
- Outside design voices (Codex + Claude subagent) produced specific, opinionated proposals that were genuinely useful for synthesis
- Competitive research via browse (Mejuri, Catbird, Vrai) grounded decisions in real-world patterns
- Iterative preview refinement caught issues early (image too tall, page too wide, too much clutter)

**What was painful:**
- Preview went through 4 iterations because the initial version was too information-dense. User wanted simplicity, not a design system showcase.
- AI mockup generation failed (no OpenAI API key configured for gstack design binary)
- First preview had light/dark toggle blocking the Cart button

**Lessons learned:**
- Start simple, add complexity only when asked. The user's design philosophy: "so easy customers don't realize they're checking out"
- For e-commerce: Image > Name > Price > Buy. Everything else is secondary.
- Always constrain max-width (1440px) on wide screens. Unconstrained layouts look broken.
- Square (1:1) product images prevent oversized cards. Avoid tall aspect ratios (3:4, 4:5) for grid items.
- Keep preview iterations fast. Show, get feedback, fix. Don't over-explain.

---

### Pre-Phase 6 — Phases 1-5 Complete (as of 2026-04-01)
**What shipped:** Full AI ops system — product intelligence, listing generation, shipping/fraud, marketing analytics, finance tracking, customer service. 126 tests passing. Deployed on Railway.

**Lessons from prior phases (consolidated):**
- Always mock `AsyncDatabase` with `AsyncMock`, not `MagicMock`
- Mock external APIs at client class level, not httpx internals
- Railway auto-deploys on push to main — be careful with what lands on main
- Cron jobs managed via cron-job.org API, not Railway native crons
- Meta Ads token expires every 60 days — monitor expiry
- Check Railway env vars before asking user for secrets
