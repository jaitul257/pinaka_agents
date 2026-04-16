# Retrospective — Pinaka Agents

Last updated: 2026-04-16

## How to Use This File
- **Read this before starting any new work.** It captures what happened, what worked, what didn't, and what to do differently.
- **Update after every push to main.** Add a new entry with: what shipped, what went well, what was painful, and lessons learned.
- **Keep entries short.** 3-5 bullets per push. This is a learning log, not a changelog.

---

## Push Log

### 2026-04-16: Phase 9.0 — Measurement Foundation (marketing agent upgrade)

**What shipped:**
- **Post-purchase attribution survey** (native, $0/mo): new `post_purchase_attribution` table + `/api/attribution/submit` endpoint + Shopify thank-you page HTML widget (Cormorant/cream styled) + `/cron/attribution-synthesize` that clusters free-text via Claude and posts weekly Slack report Monday 9:30 AM ET.
- **CAPI event enrichment**: `send_view_content`, `send_add_to_cart`, `send_initiate_checkout` helpers; enriched Purchase event with `fbp/fbc/num_items/order_id/source_url`; new `/api/pixel/event` relay so storefront can fire server-side events.
- **MER metric**: `calculate_mer()` in ads.py; weekly Slack report leads with MER over ROAS ("the honest one").
- **Microsoft Clarity integration**: conditional script tag in theme.liquid gated on new theme setting (free session recording, zero cost).
- **Marketing agent prompt rewrite**: measurement-first rule (MER > platform ROAS > post-purchase survey), retargeting-heavy allocation flip (47/40/13 from prospecting-heavy), ATC optimization + 28d click baked in as defaults.
- **37 new tests** (263 → 300, all passing). Migration applied to Supabase, cron-job.org entry created (jobId 7494627), theme pushed live.

**What went well:**
- Low-budget stack held — $0/mo operational cost vs. estimated $144+/mo for Fairing + Klaviyo + Motion. All built on existing SendGrid, Supabase, Anthropic.
- Research-first approach (3 parallel agents on Mejuri / Lukson / DTC landscape) surfaced the real unlock (ATC optimization at low event volume) before writing any code.
- `supabase db push` applied the migration cleanly on first try after the 2026-04-05 `migration repair` lesson stuck.
- All units shipped with tests that caught the MER field default mismatch and CAPI signature changes before merge.

**What was painful:**
- **Meta blocks optimization + attribution edits on published ad sets.** Script got two back-to-back rejections: "Can't Make Edits to Published Ad Set" and "Attribution Window Update Is No Longer Supported." The only path to switch is creating a new ad set, which doubles effective budget during overlap. Script was demoted from --apply to inspection-only.
- **Lukson (lukson.co) is not a peer.** Founder named them as a reference but they're an Indian fast-fashion lab-grown play ($50-180 AOV, 19% permanent "sale" anchor, JK Star backing) — a full order of magnitude below Pinaka. Research time would have been better spent on Vrai, Catbird, Aurate, Mateo.
- **Shopify Order Status Page Additional Scripts has NO Admin API.** Spent effort confirming there's no CLI path for Basic-plan stores short of scaffolding a full Node/Shopify app extension (Checkout UI Extension with `purchase.thank-you.block.render` target) — which is 30-60 min + permanent Node-in-Python-repo. 30-second admin paste won on tradeoff.
- Shopify theme `settings_schema.json` rejects blank `default` on text inputs now. Replaced with `info` field.

**Lessons learned:**
- **Meta's "edit" surface shrinks quarterly.** After a couple of years, basically every meaningful Ad Set field gets locked to creation-time only. Always check `shopify theme push --dry-run`-equivalent for Meta (i.e. a dry-run inspection script) before attempting write operations on live ad infrastructure. The pattern: read → compare → write with fallback on "immutable field" errors.
- **MER > ROAS at 1-2 orders/week.** Platform ROAS is noise at our event volume. The weekly Slack report change (MER first, ROAS second, with a "healthy DTC target 3-5x" explainer) resets the founder's mental model for budget decisions. Don't trust what you can't measure independently.
- **Native > SaaS at our stage.** Fairing costs $99/mo for a post-purchase survey. We shipped the same capability in a 120-line HTML snippet + a migration + a cron. Default to native whenever the data is ours and the tool is a thin CRUD layer.
- **Retargeting 47% vs Prospecting 40% is counterintuitive but correct for $5K AOV.** Industry defaults push 60%+ prospecting. At 1-2 orders/week with a 30-day consideration window, every cold-only dollar without warm follow-up is wasted.
- **Document-it-yourself > paste-it-somewhere for Shopify CLI.** When a step like "paste to admin" has no API and no CLI path, the winning design is: check the snippet into the repo (`shopify-theme/order-status-additional-scripts.html`) with clear paste instructions at the top. Version-controlled, reviewable, survives account handoffs.

---

### 2026-04-12 — 2026-04-16: Meta Ads Launch, Checkout Flow, Crafting Bug, Cleanup

**What shipped:**
- **Meta Ads live**: Campaign + Ad Set activated, pixel linked to ad account, Ad Variant A created and serving impressions. Ad Variant B fixed (old creative had 404 landing page URL, recreated with correct URL + SHOP_NOW CTA).
- **Meta Ads audit**: Fixed spend cap ($0 → removed), destination type (UNDEFINED → WEBSITE), added interest targeting (Jewelry + Luxury goods with Advantage+ audience), verified domain flagged as missing.
- **Full checkout flow test**: Test order → webhook fired → Order Ops Agent processed (success, 3 Claude calls, 11K tokens) → audit log written. Meta CAPI token was expired, regenerated.
- **Crafting update cron fix**: Was calling Claude with empty customer_message causing confused Slack posts. Replaced with templated email body. DB query now bounded (3-day window) so old/cancelled orders don't re-trigger. Test orders marked cancelled in Supabase.
- **Virtual try-on (attempted + reverted)**: Built Freepik Ideogram-based try-on (upload wrist → AI composites bracelet). Worked technically but AI-generated bracelet didn't match actual products. Removed from live site. User will evaluate iAugment Shopify app (free tier) instead.
- **"Free Lifetime Care" removed**: Stripped from all modules — listings generator, brand DNA, ad generator, dashboard, trust badges, tests. Website now shows 3 trust badges instead of 4.

**What went well:**
- Checkout flow test verified the complete pipeline: webhook → agent → audit in 14 seconds. Confidence that real orders will process correctly.
- Meta Ads audit caught 7 issues in one systematic pass. Interest targeting + destination type + spend cap all fixed via API in minutes.
- Crafting update fix was clean — removing the Claude call (unnecessary for templated emails) was simpler and more reliable than trying to fix the prompt.

**What was painful:**
- **Freepik IP blocking on Railway.** Free trial rate limit exhaustion caused Railway's IP to be blocked with 403 "suspicious activity." Even after upgrading to paid plan ($20/mo), the block persisted. Required `railway redeploy` to get a new IP. Block returned on next auto-deploy. Fragile.
- **AI try-on quality.** Three rounds of iteration: (1) wrong bracelet + wrong placement, (2) better placement but still generic AI bracelet, (3) product-specific prompts improved but user judged it "not realistic." AI inpainting cannot reliably reproduce the exact product — it always "interprets" rather than "overlays."
- **OpenAI billing limit.** Attempted to switch to OpenAI gpt-image-1 for try-on. Hit `billing_hard_limit_reached` immediately. Both Freepik and OpenAI were blocked simultaneously.
- **Meta pixel not linked to ad account.** Pixel existed in Business Portfolio but wasn't shared with the ad account. API couldn't share it (required business admin role). Needed manual action in Business Settings UI.
- **Shopify product `test: true` flag suppresses webhooks.** First test order with `test: True` didn't fire the orders/create webhook. Had to create a second order without the flag.

**Lessons learned:**
- **AI image editing cannot replace AR overlay for try-on.** AI generates its interpretation of a bracelet, not your actual product. For try-on, use AR overlay (Camweara, iAugment) which warps your real product image onto the wrist. AI is for creative generation (stories, ads), not product fidelity.
- **Freepik IP blocks are persistent.** Once Railway's IP is blocked, it stays blocked across auto-deploys. Only `railway redeploy` (which provisions a new instance) sometimes gets a new IP. Not reliable for production. Consider routing Freepik calls through a proxy or Cloudflare Worker if using it server-side.
- **Don't use `test: True` for webhook testing.** Shopify suppresses webhooks for test orders. Use regular Admin API orders with `financial_status: paid` and cancel them after.
- **Crafting updates don't need AI drafting.** Templated emails are more reliable and cheaper than Claude-drafted content for routine lifecycle messages. Save AI for responses that require reasoning (customer inquiries, complaints).
- **Always check both `status` and `published_at` and now also `pixel linking` before expecting Meta ads to work.** Meta has many independent prerequisites that all need to be green: payment method, campaign active, ad set active, pixel linked to ad account, domain verified, creative format compatible with placements.
- **Systematic audits catch more than ad-hoc fixes.** The Meta Ads audit (account health → campaign → ad set → ads → pixel → page → domain) found 7 issues in one pass. Would have taken days to discover them individually.

---

### 2026-04-10 — Phase 8.4: Product Pipeline, Hero Video, AI Asset Research, Concierge Bugfix

**What shipped:**
- **Product pipeline dashboard** (`/dashboard/pipeline`): 13 bracelets extracted from `BraceletsbyPinaka.pdf` with metadata (SKU, style, metal, carats, gemstone). Download base image → Pomelli manually → upload lifestyle shots → one-click create on Shopify as draft with Metal × Wrist Size variant matrix.
- **Hero video** on pinakajewellery.com homepage: Pomelli-generated 9:16 portrait video (720x1280, 914KB, 8 sec), autoplay/loop/muted/playsinline, aspect-ratio 9/16 container with cream gradient background.
- **Mobile UX**: Dark mode toggle + chat button now scroll-hide (slide off on scroll down, reappear on scroll up), aligned at same bottom level, chat panel full-screen on mobile.
- **Abandoned cart fix**: Added `mark_abandoned_carts()` database method + cron transitions "created" → "abandoned" after 60 min. Previously nothing transitioned these states, so recovery cron always saw zero carts.
- **Concierge bugfix**: Shopify renamed `search_shop_catalog` → `search_catalog` in Storefront MCP. Concierge was silently catching "Tool not found" errors and letting Claude hallucinate that products didn't exist. Fixed tool name, price parsing (cents→dollars), image extraction from nested `media` array.
- **Dashboard status fix**: Added Active/Draft dropdown to product forms. Previously edit form never sent `status` to Shopify, so ticking "active" had no effect. Also added `published_at` auto-set when status flips to active (Shopify requires BOTH for storefront visibility).
- **Freepik AI asset research**: Integrated API key, tested Kling o1 (video), Mystic (image), Flux Pro (image). Discovered that real photographer vocabulary (Hasselblad X2D, 120mm macro, f/11, ISO 100, Kodak Portra 400) produces dramatically more realistic results than AI buzzwords (cinematic, ultra-detailed, magnific, 8K).

**What went well:**
- **PDF extraction** worked on first try with pymupdf — 13 bracelet images + metadata parsed cleanly.
- **Freepik Flux Pro + anti-AI vocab** produced near-indistinguishable-from-real product photography. The research-backed prompt template (camera body + lens + aperture + ISO + film stock) was a massive quality unlock vs. earlier Mystic attempts.
- **Concierge bug detection**: Tested MCP endpoint directly once symptoms appeared, found `tools/list` endpoint, discovered renamed tool in under 5 min. Silent exception handler now logs warnings on isError/non-200.
- **Hero video layout iteration** was fast once we committed to 9:16 aspect ratio container (matches portrait source video, zero cropping).

**What was painful:**
- **Shopify section render cache is brutal**. After 4+ theme pushes with fresh content, the `etag: page_cache` kept serving stale HTML. Even pushing `<h1>TEST CACHE BUST</h1>` to the section file didn't flush it. Only `shopify theme push` from INSIDE the `shopify-theme/` directory worked — pushing from the repo root silently no-op'd.
- **Shopify product visibility has two flags**: `status: active` alone is not enough. Need `published_at` ALSO set. Found by checking Shopify API product response — `status: active` but `published_at: null` means the product is not live on the storefront.
- **Freepik free trial rate limit**: Hit after 30 successful tasks (6/13 bracelets completed). Error message doesn't warn before cutoff. Need paid plan to continue.
- **Shopify CLI `--only` flag silently no-ops** when run from wrong directory. No error, push "succeeds", but files don't update. Learned to always `cd shopify-theme/` before push.
- **AI image generation buzzword trap**: First Mystic attempts with "cinematic/ultra-detailed/8K/magnific" looked fake. Research showed these tokens are tagged on AI training data as stylized, so the model associates them with the AI look. Stripping buzzwords + adding real gear names was the fix.
- **Pipeline status regression**: Initial pipeline publish created products as draft with single "Default" variant at $0. User couldn't edit prices because variant_options wasn't in Supabase, so edit form had no metals/sizes checked. Fixed by making pipeline publish create full Metal × Wrist Size matrix.
- **Carat string parser**: Naive `float(carats.replace("CT", ""))` broke on colored diamond format "White 1.35CT + Blue 1.80CT". Fixed with regex that extracts all numbers before "CT" and sums them.

**Lessons learned:**
- **AI realism is about vocabulary, not settings.** Mystic maxed out with "magnific_sharpy + 4k + creative_detailing 50" produced worse realism than Flux Pro with plain prompts using real camera/lens/ISO/film stock names. Training data caption patterns matter more than resolution knobs.
- **Shopify active ≠ published.** Always set `published_at` when flipping status to active on products. Otherwise the product exists but isn't on the storefront.
- **Shopify MCP tools are implicitly versioned.** Tool names can change with no notice. Silent exception handlers hide these failures for weeks. Every MCP call should log warnings on error responses, not just catch Exception and return [].
- **Shopify CDN page cache doesn't bust on theme push alone** — requires template/settings_data.json edits OR Theme Editor Save click in admin OR time. Section file changes alone don't invalidate the page render cache.
- **Run `shopify theme push` from inside `shopify-theme/`.** Don't run from repo root with `cd shopify-theme &&` inline — the `--only` flag path resolution is different and can silently no-op.
- **PDF image extraction is underrated.** pymupdf parsed 13 images + structured metadata in 30 seconds. No OCR, no manual cataloging.
- **Pomelli is manual, but the 90% around it can be automated.** Pipeline = catalog extraction + manual Pomelli step + upload back + Shopify create. User does 3 min per product instead of 30.

---

### 2026-04-08 — Phase 8.1-8.3: Agent Upgrades, Awareness, Marketing Strategy

**What shipped:**
- **6 agent upgrades**: Confidence scoring (high/medium/low + auto-escalate), cross-agent feedback loop (finance → marketing), customer memory (past 10 interactions), token optimization (51% reduction on Order Ops), Slack Block Kit formatting, storefront AI concierge chat widget
- **Heartbeat awareness system**: `observations` table + `/cron/heartbeat` every 30 min. 5 cheap SQL checks (stuck orders, unanswered messages, shipping delays, unacted observations, agent failures). Claude only invoked when issues found.
- **Marketing strategy**: Full-funnel 3-campaign structure (Prospecting $40/day, Retargeting $25/day, Retention $10/day). Seasonal calendar (6 windows). Margin-driven budget allocation. `/cron/marketing-snapshot` every 6h (data only, $0 LLM), `/cron/marketing-weekly` Monday 9AM (Claude strategy review).
- **Storefront chat widget**: Gold chat bubble on every page of pinakajewellery.com. Claude + Shopify MCP for product search. Product cards, follow-up suggestions, mobile responsive.
- **About page**: Custom section + template pushed to theme. Page created in Shopify admin.
- **Dashboard fixes**: Edit button per product (loads from Shopify API), per-size pricing fix (HTML quote escaping), variant_options column added.
- **14 active cron jobs** on cron-job.org including heartbeat, marketing snapshot, weekly strategy.

**What went well:**
- Research-first approach for awareness system — the heartbeat + observations pattern from industry research was exactly right. Cheap SQL checks first, LLM only when needed.
- Token optimization halved Order Ops cost (31K → 15K tokens) just by reducing max_tokens and trimming context nulls.
- Marketing cadence research prevented a mistake — 30-min marketing cron would have hurt Meta learning phase. Weekly is correct for $75/day high-AOV.
- All 5 agents validated in production with real Claude calls. Complaint escalation worked correctly (agent chose to post to Slack instead of emailing).

**What was painful:**
- Dashboard pricing bug took 3 iterations to find. Root cause: `"` in HTML attribute `value="6""` broke the value. Fix: single-quoted attributes `value='6"'`.
- Audit logger had sync/async mismatch — `await` on a sync Supabase call. Worked on Railway (via asyncio.to_thread wrapper) but failed locally.
- Agent tool parameter mismatch (`order_id` vs `shopify_order_id`) wasn't caught by tests — only surfaced in production. Need integration tests that exercise tool wrappers with real parameter shapes.
- AGENT_ENABLED env var required a redeploy to take effect — Railway doesn't hot-reload env vars into running processes.
- Supabase migration naming: `007_name.sql` gets skipped, must use `<timestamp>_name.sql` pattern. Same lesson from Phase 8 — now firmly in CLAUDE.md.

**Lessons learned:**
- **Marketing at low volume: weekly > daily.** At 1-2 purchases/week, daily optimization is noise-chasing. 15% ROAS penalty from over-tinkering is documented.
- **Agent awareness needs a perception layer.** Raw webhooks are events, not awareness. The `observations` table converts events into business-level observations that agents can reason about.
- **Cheap checks first, LLM when needed.** The heartbeat runs 48 SQL checks/day at $0. Claude fires maybe 1-2 times/day when real issues exist. 99% of beats cost nothing.
- **HTML attribute quoting matters for form values.** Any value containing special characters (`"`, `'`, `<`, `>`) needs proper escaping. Single-quoted attributes or `&quot;` entities.
- **Test tool wrappers with the actual parameter names Claude will send.** Unit tests mocked at too high a level and missed the name mismatch.

---

### 2026-04-07 — Phase 8: Agentic Layer

**What shipped:**
- **Agent framework** (`src/agents/`): BaseAgent with Claude tool_use reasoning loop, ToolRegistry (17 tools wrapping existing functions), PolicyEngine (7 guardrail policies), ContextAssembler, AuditLogger
- **5 specialized agents**: Order Ops, Customer Service, Marketing, Finance, Retention — each with domain-specific system prompts and tool sets
- **Dual-path integration**: `shopify_webhooks.py` routes through agent when `agent_enabled=True`, falls back to procedural on failure. Existing flow unchanged when flag is off.
- **35 new tests** (232 total, all passing). Tests cover ToolRegistry, all 7 policies, BaseAgent loop (text, tool_use, deny, escalate, max_turns, error recovery).
- **`agent_audit_log` table** live on Supabase with 3 indexes. Migration applied via `supabase db push`.

**What went well:**
- Research-first approach: reading Anthropic's building-effective-agents guide, Shopify agent ecosystem, and real implementations before coding led to better architecture decisions.
- Raw API loop over tool_runner/Agent SDK — gives full control over guardrail injection. Tool_runner can be adopted later.
- Wrapping existing functions as tools means zero business logic rewriting. All 39 action functions stay as-is.
- 232 tests all green first try — no regressions from the dual-path webhook changes.

**Lessons learned:**
- Supabase migration filenames must match `<timestamp>_name.sql` pattern. Old-style `007_name.sql` gets skipped by `supabase db push`. Used `20260407120000_agent_audit.sql`.
- `is_error: True` on tool results is critical — Claude adapts and tries alternatives instead of crashing the loop.
- Tool descriptions need prompt engineering equal to system prompts (Anthropic's advice). Each tool got a detailed description telling Claude when/how to use it.
- Three-layer guardrail model (input validation, execution controls, output filtering) is cleaner than a flat policy list.
- `agent_enabled` defaults to False — zero risk to production until explicitly flipped on Railway.

---

### 2026-04-07 — Homepage sections, PDP variants, design system alignment

**What shipped:**
- **Homepage**: Trust Badges (3 badges), Atelier Ledger ("Recently Crafted" with 4 entries), Craft Timeline, Founder Note all live via updated index.json
- **PDP Metal + Wrist Size variants**: 12 Shopify variants (3 metals x 4 sizes) created via REST API, Dawn's built-in pill picker auto-renders
- **PDP Typography**: Cormorant Garamond 36px title, Geist Mono 26px price, DM Sans 16px body, matching design-consultation-preview exactly
- **Variant pills**: transparent bg, 1px border, 8px 18px padding, 12px uppercase muted labels, gold selected state
- **Dashboard multi-variant support**: Metal checkboxes + Wrist Size checkboxes with per-size pricing, auto-generates variant matrix for Shopify push
- **Trust badges updated**: removed "Free Lifetime Care", changed "Lab-Grown Diamonds" to "Lab-Grown & Natural"
- 197 tests still passing after dashboard changes

**What went well:**
- Shopify theme settings already had Cormorant + DM Sans configured (`cormorant_n4`, `dm_sans_n4`). No need for Google Fonts duplication, just Geist Mono.
- Dawn's variant picker with `picker_type: "button"` works out of the box. Adding options to the product was all that was needed.
- Pushing `config/settings_data.json` alongside other files forced Shopify to recompile all templates immediately, bypassing the CDN cache issue.

**What was painful:**
- **Shopify CDN caching was the #1 blocker.** Template and asset changes pushed via CLI reported "success" but didn't appear on the storefront for 15-30+ minutes. The compiled template version (`t/3`) and CSS `?v=` hash are baked into the compiled theme and DON'T update until Shopify recompiles.
- **The `--only` flag without `--nodelete` DELETES files not in the filter.** One push accidentally removed Dawn's built-in snippets, breaking the preview URL with Liquid errors. Had to do a full theme restore immediately.
- **Multiple redundant font loads**: Shopify CDN (via settings), Google Fonts in theme.liquid, Google Fonts in section. Consolidated to: Shopify CDN for Cormorant + DM Sans, Google Fonts only for Geist Mono.
- **Tried 5 approaches** before finding the settings_data.json trick: waiting for propagation, preview URLs, inline style sections, custom_liquid blocks, renamed CSS files.

**Lessons learned:**
- **To force Shopify CDN recompilation, push `config/settings_data.json` alongside your changes.** This is the only reliable way to get immediate propagation. The settings file triggers a full theme recompile on Shopify's side.
- **Never use `shopify theme push --only` without `--nodelete`.** The default behavior deletes all remote files NOT matching the `--only` filter. Always add `--nodelete` for partial pushes.
- **Don't duplicate font loading.** Check `config/settings_data.json` for `type_header_font` and `type_body_font` before adding Google Fonts links. Shopify already loads these via `font_face` in theme.liquid.
- **Use Shopify CSS variables instead of hardcoded font names.** `var(--font-heading-family)` works because settings_data.json sets the font. Override size/weight only, not font-family.
- **The `pinaka-pdp-styles` section approach (inline `<style>` in a template section) works but is fragile.** It depends on product.json propagating. Putting CSS in the global `pinaka-custom.css` asset is more reliable since it loads via theme.liquid.
- **Shopify REST Admin API works for product variant creation without `write_products` scope in shopify.app.toml.** The Railway access token has broader scopes than the app manifest declares.
- **Shopify's `cormorant_n4` is "Cormorant", NOT "Cormorant Garamond".** These are different typefaces with different letterforms. The design system uses Cormorant Garamond from Google Fonts. Must explicitly load from Google Fonts and override `font-family` — can't rely on Shopify's built-in font.
- **Always set `-webkit-font-smoothing: antialiased` on body.** Without it, fonts render heavier/blurrier on macOS. The design mockup has it, Dawn doesn't.
- **Dark mode needs explicit overrides for EVERY custom section.** Dawn's dark mode only covers its own components. Trust badges, variant pills, craft timeline, ledger — all need `body.dark-mode` rules for text color, borders, and backgrounds. Don't assume Dawn handles it.
- **Dark mode header: use same bg as body (`#1a1815`), remove ALL borders.** The announcement bar has a subtle `border-bottom` that creates a visible line. Must `border: none !important` on `.announcement-bar`, `.utility-bar`, `.header`, and `.header-wrapper`.
- **Compare computed styles element-by-element against the design source.** Use `getComputedStyle()` audit on every PDP element (h1, price, desc, labels, pills, button) to find exact mismatches. Font-family, font-size, font-weight, margin, padding, color, background — check ALL of them. The devil is in the details (Cormorant vs Cormorant Garamond, 0px vs 6px margin, auto vs antialiased smoothing).

---

### 2026-04-05 — Phase 6.2: Auto-create Meta Ad on Go Live + 6.1 polish

**What shipped:**
- **Phase 6.2 complete**: "Go Live" button in dashboard now does creative ACTIVE → create Ad object under default Ad Set → persist ad_id → deep-link the card into Ads Manager. Collapses the manual "attach creative to ad set" trip that used to be required after Phase 6.1.
- Bootstrapped Pinaka's ad account via API: Campaign `120244523278190359` (OUTCOME_SALES, PAUSED) + Ad Set `120244523287540359` (US 25-65, $25/day, purchase-optimized, PAUSED). Stored as `META_DEFAULT_CAMPAIGN_ID` + `META_DEFAULT_ADSET_ID` on Railway.
- `MetaCreativeClient.create_ad()` — POSTs to `/act_{id}/ads` with full error handling including rich error extraction
- Migration 009: `meta_ad_id` + `meta_adset_id` columns on ad_creatives
- `_extract_meta_error()` helper — surfaces `error_user_title` + `error_user_msg` (the fields Meta hides the actual error behind), not just the useless generic `message`
- **Phase 6.1 polish**: fixed status tracking bug where "Go Live" flipped Meta to ACTIVE but the dashboard kept rendering "Published (Paused on Meta)" because the DB state never changed. Added `live` status value via migration 008.
- Fixed creative ID truncation — two places were slicing display to 12 chars, hiding the last 3 digits of 15-digit Meta IDs, making them useless for Ads Manager lookups.
- **Supabase image sync fix**: `/cron/sync-shopify-products` was only embedding to ChromaDB, never writing `products.images` to Supabase. First real end-to-end test failed with "Product has no images" because no code path ever backfilled from Shopify's `images[].src`. Added cron backfill + inline lazy backfill in dashboard generate handler as a fallback for new products.
- Real Claude generation + Meta push live end-to-end: Variant A pushed as creative `959138700395572`, flipped ACTIVE successfully. Variant B pushed as creative `1679259843246920`, ACTIVE, but Ad creation blocked on payment method.
- 197 tests green (was 186): 10 new unit tests for `create_ad`, 2 integration tests for Go Live Ad auto-creation flow, 1 regression test for `error_user_title` extraction.

**What went well:**
- **Real end-to-end testing surfaced three bugs no test could have caught**: Meta v21→v25 deprecation mid-session, the `published`/`live` status tracking gap, and the creative ID display truncation. All in <20 min after starting to exercise the live dashboard.
- `_extract_meta_error()` turned a useless "Invalid parameter" into the actionable "No Payment Method: Update payment method at billing hub." Write once, pays off for every future Meta 4xx.
- Backwards-compat fallback in Go Live: if `META_DEFAULT_ADSET_ID` is empty, skip Ad creation and just flip the creative. Means tests and staging work without requiring a real ad set, and first-time setup is soft-gated on an env var instead of crashing.
- `supabase migration repair --status applied 001 002 003 004 007` unblocked a migration history that was out of sync from previous manual SQL Editor runs. Cleaner than hand-editing `supabase_migrations.schema_migrations`.
- Full session: from "lets one shot it with 6.2" to deployed + smoke tested in a single push. No intermediate reviews, no plan file, no outside voices. Trusted the existing Phase 6.1 foundation.

**What was painful:**
- **Meta v25.0 API quirks that only surfaced via direct API calls** — none are documented in the official docs:
  - Campaign creation now requires explicit `is_adset_budget_sharing_enabled=false` when not using campaign budget
  - Ad Set creation requires `targeting_automation.advantage_audience=1` or explicit disable, no silent default
  - Advantage+ audience forces `age_max >= 65` (can't do 25-54 targeting with advantage_audience on)
  - Ad creation requires a payment method on the ad account even for PAUSED Ads under a PAUSED Ad Set
- **Supabase CLI migration history was out of sync** because earlier migrations were applied manually via SQL Editor. `supabase db push` tried to re-run 001 from scratch and hit "relation already exists". Fix was `migration repair` to mark history without running.
- **User confused Claude.ai Pro/Max with Anthropic API billing.** Spent a few back-and-forth turns clarifying that the two products have separate billing at different URLs (claude.ai vs console.anthropic.com). User had added funds to Pro plan thinking it would unblock the API. Needed to explain the distinction clearly before the smoke test could run with real Claude output.
- **Variant A first go-live attempt was actually broken in two ways**, but only the user's screenshot revealed it. From inside the code it "worked" — we flipped Meta to ACTIVE and logged success. The UI still said "PAUSED ON META" though, and the ID was truncated. Lesson: `curl /GET` against the actual Meta resource is the only real smoke test for write operations. Logs lie.
- **Ran out of Anthropic credits mid-session** — the first real generation attempt hit "Your credit balance is too low". Had to work around with synthetic variants for the smoke test, then user added $5 to unblock. Sonnet 4 cost: ~$0.02 per 3-variant generation, so $5 = ~250 batches which is plenty of runway.

**Lessons learned:**
- **Meta API errors put the useful detail in `error_user_title` + `error_user_msg`, NOT in `message`.** The `message` field is almost always a useless generic like "Invalid parameter" or "Unsupported request". Always grab the user-facing fields first, fall back to `message`. Add this to every Meta client helper from day one — don't wait for it to bite.
- **Creative `status` on Meta is NOT what makes ads serve impressions.** Creatives are reusable assets in Creative Library with their own lifecycle (DRAFT/ACTIVE/DELETED). Ads are the objects that serve impressions. Ad Sets contain Ads. Campaigns contain Ad Sets. All three levels have independent status flags, and ALL three must be ACTIVE for impressions to flow. Phase 6.1 shipped with the assumption that "flip creative to ACTIVE = ads are running", which is wrong. Phase 6.2 corrects this by also creating the Ad object.
- **Logs lie, GET requests don't.** When a write API call returns 200, don't trust the response — follow up with a GET on the resource and inspect the real state. This caught the status-tracking bug immediately (Meta said ACTIVE, dashboard said PAUSED) and surfaced the truncated ID.
- **"Ship to learn" works when the foundation is solid.** Phase 6.1's test coverage (60 tests around the ad creative flow) meant Phase 6.2 could drop in without breaking anything. The 10 new tests for Phase 6.2 slotted into the existing mock patterns cleanly. Lesson: invest heavily in the first version's test scaffolding; future iterations are free.
- **Backwards-compat fallback is cheap insurance.** `is_meta_ad_ready` property defaults to False when `META_DEFAULT_ADSET_ID` is empty. Go Live still works in creative-only mode for anyone without the Phase 6.2 setup. 3 lines of code, saves a deploy rollback.
- **The lazy-backfill-plus-cron pattern is the right way to sync derived state from external APIs.** Cron runs every 15 min for correctness. Dashboard button path does an inline fetch when it needs fresh data now. User never sees staleness, cron catches the batch case.

**Pending human steps (must complete to unblock full 6.2):**
1. **Add payment method** to Meta Ad Account at https://business.facebook.com/billing_hub/accounts/details/?asset_id=27080581041558231 — currently blocks Ad creation entirely. No card = no ads, even paused.
2. **Flip default Ad Set `120244523287540359` from PAUSED → ACTIVE** in Meta Ads Manager after payment method is added. One-time setup; subsequent Go Live clicks serve impressions with zero manual steps.
3. **Flip default Campaign `120244523278190359` from PAUSED → ACTIVE** in Meta Ads Manager. Also one-time.

**State as of end-of-session:**
- 2 creatives live in Meta Creative Library (ACTIVE): Variant A `959138700395572`, Variant B `1679259843246920`
- 0 Ads created (blocked on payment method)
- 1 creative still pending_review: Variant C `3-carat lab-grown diamonds`
- Full pipeline end-to-end-testable as soon as payment method is on file

---

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
