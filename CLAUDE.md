# CLAUDE.md

## Project Overview

Pinaka Agents is an AI-powered autonomous operations system for **Pinaka Jewellery**, a premium handcrafted diamond tennis bracelet DTC brand on Shopify. The system handles the full e-commerce lifecycle: orders, shipping, customer service, finance, marketing, and product management, with human-in-the-loop Slack approvals.

**Stack:** Python 3.12 + FastAPI + Supabase (PostgreSQL) + Railway
**AI:** Claude Sonnet 4 (drafts, classification) + OpenAI text-embedding-3-small (RAG)
**Integrations:** Shopify, SendGrid, Slack, ShipStation, Meta Ads/CAPI, Google Ads/Merchant

## Business Details

| Key | Value |
|-----|-------|
| **Brand** | Pinaka Jewellery |
| **Product** | Premium handcrafted diamond tennis bracelets |
| **Founder** | Jaitul |
| **Website (customer-facing)** | https://pinakajewellery.com (custom domain, live on Cloudflare DNS) |
| **Shopify Admin** | https://admin.shopify.com/store/pinaka-jewellery |
| **Shopify Domain (Admin API)** | pinaka-jewellery.myshopify.com |
| **Google Merchant Center ID** | 5759598456 |
| **Shopify API Version** | 2025-01 |
| **Email From** | hello@pinakajewellery.com (Jaitul at Pinaka Jewellery) |
| **Railway App URL** | https://pinaka-agents-production-198b5.up.railway.app |
| **Supabase Project** | fhtzzpklpkdfptxrxlek |
| **Slack Channel** | C0APKCZDCTG |
| **Meta Pixel ID** | 1422946742915513 |
| **Meta Ad Account** | act_27080581041558231 |
| **Meta Business Portfolio** | 1035697978984161 |
| **Meta Catalog ID** | 2850427255291757 |
| **Meta App (Marketing)** | Pinaka Marketing (ID: 930736393145618) |
| **Cron Dashboard** | https://console.cron-job.org/dashboard |
| **GitHub Repo** | https://github.com/jaitul257/pinaka_agents |
| **Business Timezone** | US/Eastern |
| **Made-to-order Lead Time** | 15 business days |
| **Daily Ad Budget Cap** | $75 |

## Architecture

```
Shopify Webhooks → FastAPI → Supabase → AI Processing → Slack Approval → Action (email/API)
                                                                        ↓
Cron Jobs (cron-job.org) → FastAPI endpoints → Business Logic → Slack/Email/DB
```

**9 modules** in `src/`:
- `agents/` — AI agent framework: BaseAgent (Claude tool_use loop), ToolRegistry, PolicyEngine (7 guardrail policies), ContextAssembler, AuditLogger, 5 specialized agents
- `api/` — FastAPI app, Shopify webhooks, inbound email handler
- `core/` — Database, settings, email, Slack, Shopify client, rate limiter, attribution
- `shipping/` — Fraud detection, insurance, ShipStation tracking, evidence collection
- `finance/` — Per-order P&L, Shopify fees, daily/weekly summaries
- `marketing/` — ROAS calculator, Meta/Google ad spend sync, CAPI, catalog feeds
- `customer/` — Message classification, AI response drafts, reorder reminders
- `product/` — Product schema, ChromaDB embeddings/RAG
- `listings/` — Claude-powered Shopify listing generator
- `dashboard/` — Streamlit dashboard (password protected)

## Key Files

| File | What it does |
|------|-------------|
| `src/api/app.py` | All routes, 15 cron endpoints, Slack webhook handler (~1200 lines) |
| `src/api/shopify_webhooks.py` | Order/refund/customer webhook processing, CAPI + Google conversion fire |
| `src/core/database.py` | AsyncDatabase wrapper + sync Database with ~40 Supabase methods |
| `src/core/settings.py` | All env vars, thresholds, rate limits (Pydantic BaseSettings) |
| `src/shipping/processor.py` | Fraud scoring, insurance validation, ShipStation integration |
| `src/marketing/ads.py` | ROAS calculator, weekly report, Slack budget buttons |
| `src/finance/calculator.py` | Order profit, Shopify fees, daily/weekly aggregation |

## Database

Supabase PostgreSQL. Migrations in `supabase/migrations/` (001-006b, 20260407-20260408).

**Core tables:** orders, customers, messages, daily_stats, products, listing_drafts, refunds, voice_examples, review_requests, agent_audit_log, observations, heartbeat_state

## Development Commands

```bash
# Run tests (242 total, all passing)
.venv/bin/python -m pytest tests/ -v

# Run specific test file
.venv/bin/pytest tests/unit/test_shipping.py -v

# Import check
.venv/bin/python3 -c "from src.api.app import app"

# Local dev server
.venv/bin/uvicorn src.api.app:app --reload --port 8000
```

## Deployment

- **Railway:** Auto-deploys on push to `main`. Config in `railway.toml`.
- **Cron jobs:** Managed via cron-job.org API (not Railway native). Config reference in `deploy/railway-crons.json`.
- **URL:** `https://pinaka-agents-production-198b5.up.railway.app`
- **Health:** `GET /health` returns per-module status.

## Business Rules (don't change without asking)

| Rule | Value | Why |
|------|-------|-----|
| Fraud: high-value threshold | $5,000 | Requires video verification |
| Fraud: velocity limit | 2 orders/24h per email | Prevent rapid-fire fraud |
| Carrier insurance cap | $2,500 | UPS/FedEx standard coverage |
| Insurance required above | $500 | Company policy |
| Shopify fees | 2.9% + $0.30 | Payment processing rate |
| ROAS increase threshold | 4.0x | Budget increase recommendation |
| ROAS maintain minimum | 2.0x | Below this = decrease budget |
| Max daily ad budget | $75 | Current budget ceiling |
| Crafting update email | Day 2-3 after order | Made-to-order production window |
| Abandoned cart delay | 60 minutes | Before sending recovery |
| Max cart recovery/week | 2 per customer | Prevent spam |
| Reorder intervals | 90, 180, 365 days | Post-purchase reminders |
| Reorder cooldown | 180 days | Min gap between reminder emails |
| Business timezone | US/Eastern | All daily_stats date boundaries |
| Made-to-order days | 15 | "Ships in X business days" |

## Slack Approval Pattern (Phase 12 — tiered)

Not every action goes through Slack anymore. Phase 12 introduced three tiers (`src/agents/approval_tiers.py`):

| Tier | What | Examples |
|------|------|----------|
| **AUTO** | Send directly, log to `auto_sent_actions` for founder review | Welcome series 1-5, crafting update, care guide reminder, reorder reminder 180/365d |
| **REVIEW** | Existing Slack approve/reject flow | Customer response, cart recovery, listing publish, ad creative, POQ batch, SEO blog, lifecycle anniversary/VIP/sunset |
| **ESCALATE** | Always surfaces to Slack with extra context | Order hold/cancel, fraud review, budget change, refund approval |

Unknown action types default to REVIEW (safe-side). Conservative v1 list — widen AUTO by observing `auto_flag_rate_30d()` over time.

15+ Slack action types. Handler at `POST /webhook/slack`.

## Agent System Design Rules (learned the hard way — don't re-break)

These are non-negotiable patterns the Phase 13 research + production incidents validated. Break one = bug.

1. **Closed-set taxonomies reject unknowns with a log warning.** Any field that routes to an action (`outcome_type`, `verdict`, `action_type`) uses a `frozenset` of allowed values. Unknown values are rejected, not silently accepted. Prevents six-months-from-now drift where someone adds `customer_was_delighted` and corrupts downstream analytics. See `src/agents/outcomes.py:OUTCOME_TYPES`, `src/agents/approval_tiers.py:AUTO_ACTIONS`.

2. **Confidence defaults to `"unknown"`, NEVER silently to `"high"`.** See `src/agents/base.py:_extract_confidence`. Missing confidence tag = warning log + "unknown" — not quietly assumed high. Prior default (`"high"`) was laundering every parser miss into a positive signal that outcome feedback would reinforce.

3. **Secondary reviewers must soft-fail PASS.** Any critic/validator/skeptic that wraps a primary flow (e.g. `src/agents/skeptic.py`) returns PASS when it can't reach its backend. Failing closed would stop every customer-service email whenever OpenAI has a hiccup. Never block a primary flow because a secondary check is down.

4. **Outcomes are program-verified only.** `src/agents/outcomes.py` writes rows from SendGrid webhooks + scheduled SQL checks. Agents CANNOT write outcomes about their own work. Kamoi TACL 2024 + Karpathy's "sucking supervision through a straw" — LLM self-grading without external ground truth degrades performance.

5. **Cross-model for critique, same-model never.** If you need a reviewer, use a DIFFERENT model. Claude drafts → GPT-4o-mini reviews. Never Claude-reviews-Claude. Karpathy LLM-Council finding: cross-model carries genuine signal, same-model self-critique doesn't. `DRAFTER_MODEL != REVIEWER_MODEL` is a lock-in test.

6. **Asymmetric critique rubrics prevent over-rejection.** Critics default to "reject to look useful." Asymmetric scoring in the prompt (+5 for a real catch, −10 for a false block) shifts the behavior. See `src/agents/skeptic.py:SYSTEM_PROMPT`.

7. **Memory is file-based, not vector-based at our scale.** 1-2 orders/week means a keyed SQL lookup beats cosine similarity on both latency AND relevance. See `src/agents/memory.py` entity_memory pattern. Only reach for ChromaDB when the entity count is in the thousands AND natural-language query is the access pattern — which for Pinaka is only the concierge's product RAG.

8. **Compacted view, raw as immutable log.** Agents read compiled markdown wikis for entities (customer / product / seasonal / agent). Raw rows in `orders`, `messages`, `agent_audit_log`, `observations` stay forever — they're the source of truth — but agents never read them during reasoning. Karpathy llm-wiki pattern: "you rarely ever write or edit the wiki manually — it's the domain of the LLM." Compile nightly via `/cron/compile-entity-memory`.

9. **Tool docstrings state units, side effects, failure modes, and WHEN NOT to call.** Tool descriptions are the agent's only documentation for the tool's behavior. Vague docs force the agent to hallucinate inputs. Every tool registered in `src/agents/*.py` should have: units (USD), idempotency status ("NOT idempotent: duplicates produce real duplicate labels"), failure-mode guidance ("returns null on webhook delay — retry, don't escalate"), and a "do not call for X" clause.

10. **System prompts should be neutral, tools carry the strategy.** A prompt that says "decide what action to take" biases toward action. Neutral framing: "review state, report findings, recommend only if warranted." Strategy lives in tool-returned data (e.g. `get_current_strategy`) so the agent reasons against LIVE data, not a memorized memo. See `src/agents/marketing.py` + `src/marketing/strategy_config.py`.

11. **Verify schema before writing queries.** Grep migrations for each column name before using it. We wasted 4 deploy cycles in Phase 13.2 on assumed-but-wrong column names (`orders.line_items`, `daily_stats.aov`, `ad_creatives.claude_brief`, `ad_creative_metrics.creative_id`). 30 seconds of `grep` prevents 30 minutes of deploy-cycle debug.

12. **Wire the route BEFORE writing the handler body.** A cron handler that imports cleanly but has no `@app.post` decorator fails silently — tests pass, curl returns 404. Learned from `b741a97` → `8a0d5ff`. Pattern: add the decorator + a one-line stub FIRST, confirm route registers, THEN write the body.

13. **Memory is opt-in via tool, NOT auto-injected into context.** `get_my_memory` (agent self-memory) and `get_entity_memory` (customer/product/seasonal) are registered as tools the agent CHOOSES to call when relevance warrants the extra tokens. They are never inserted into the default context. Reason: most agent runs are cold-utility (single-order webhook processing, lifecycle eligibility check) where a 500-word memory note adds no signal, only 10% context bloat. Karpathy's "smallest set of high-signal tokens" is the principle. If we later observe that agents never call the tool when they should, tighten the tool's when-to-call guidance — don't switch to auto-inject.

14. **Every agent-attributable email carries SendGrid `custom_args`.** `EmailSender.send()` accepts `custom_args={agent_name, action_type, audit_log_id, entity_type, entity_id}` — all agent-caused wrappers (`send_crafting_update`, `send_welcome_email`, `send_lifecycle_email`, `send_cart_recovery`, `send_service_reply`, `send_reorder_reminder`) set them. Without attribution, SendGrid events (`/webhook/sendgrid`) can't correlate back to the agent run that caused the email, and `outcomes` rows become anonymous. See `src/core/email.py:_build_email_context`. If you add a new email path, set attribution or outcomes data becomes unusable.

15. **All datetimes are timezone-aware; parse with `"Z" → "+00:00"`.** Never use `datetime.utcnow()` (deprecated in 3.12, naive, crashes when subtracted from Supabase's tz-aware TIMESTAMPTZ values). Use `datetime.now(timezone.utc)` for current time. Parse stored timestamps with `datetime.fromisoformat(s.replace("Z", "+00:00"))` and fall back to `replace(tzinfo=timezone.utc)` when `tzinfo is None` (old rows may be naive). `datetime.fromisoformat(s.replace("Z", ""))` — the old pattern — silently drifts time by whatever offset the server is in and is always wrong.

16. **Surface evidence, never auto-mutate policy.** Signals that could suggest policy changes (tier promotion, rubric tuning, model swap) write observations to `observations` (severity warning/info) — they do NOT edit the policy file. Founder reviews via heartbeat → Slack → hand-edits `src/agents/approval_tiers.py` etc. See `src/agents/tier_audit.py` for the canonical pattern. Applies anywhere an automated audit might be tempted to "self-heal" — don't.

## Testing Conventions

- Unit tests mock `AsyncDatabase` (not `Database`)
- Use `AsyncMock()` for DB mock instances (not `MagicMock`)
- Async test functions auto-detected (pytest-asyncio mode=AUTO)
- Integration tests use FastAPI TestClient with `@patch` decorators
- Mock at client class level for external APIs (not httpx internals)

## Environment Variables

All defined in `src/core/settings.py`. Template in `.env.example`.
Secrets live on Railway, never in code. Service keys use `service_role` Supabase key.

**IMPORTANT:** Railway CLI is available. Before asking the user for any env var, secret, API key, or connection string, always try to fetch it from Railway first:
```bash
# Get a specific variable
railway variables --json | python3 -c "import json,sys; print(json.load(sys.stdin).get('VAR_NAME',''))"

# Get all variables
railway variables --json

# Set a variable
railway variables set VAR_NAME=value
```
Only ask the user if the variable is not set on Railway or needs to be created for the first time.

## Phase History

| Phase | What shipped |
|-------|-------------|
| 1 | Product schema, embeddings, RAG, listing generator |
| 2 | Fraud detection, insurance validation, ShipStation integration |
| 3 | Order webhooks, tracking, delivery emails, evidence collection |
| 4 | Refund pipeline, webhook health monitoring, Meta CAPI, reorder reminders, product dashboard |
| 5 | AsyncDatabase, attribution capture, ad spend sync (Meta/Google), product catalog feeds, Google offline conversions, ROAS cron |
| 6.0-6.2 | Ad creative generation (Claude), Meta Creative Library push, Go-Live ad creation, dashboard ad management |
| 7 | Storefront: homepage sections (trust badges, atelier ledger, craft timeline), PDP Metal/Wrist Size variants (12 combos), design system alignment (Cormorant Garamond/Geist Mono/DM Sans), dark mode, dashboard multi-variant support with per-size pricing |
| 8.0 | Agentic layer: BaseAgent (Claude tool_use loop), ToolRegistry, PolicyEngine (7 guardrails), ContextAssembler, AuditLogger, 5 agents (Order Ops, Customer Service, Marketing, Finance, Retention), dual-path webhooks, agent_audit_log table |
| 8.1 | Agent upgrades: confidence scoring, cross-agent feedback loop (finance → marketing), customer memory (past interactions), token optimization (51% reduction), Slack Block Kit, storefront AI concierge chat widget |
| 8.2 | Agent awareness: observations table, heartbeat monitor (30-min cron, cheap SQL checks, Claude only when issues found), observation writers in webhooks |
| 8.3 | Marketing strategy: 3-campaign funnel (Prospecting/Retargeting/Retention), seasonal calendar (6 windows), margin-driven budget, 6h data snapshots, Monday 9AM weekly strategy review |
| 8.4 | Product pipeline dashboard (PDF extraction → Pomelli → Shopify with variant matrix), hero video on homepage, mobile UX scroll-hide, abandoned cart flow fix (mark_abandoned_carts transition), concierge MCP bugfix (search_shop_catalog → search_catalog), product status/published_at fix, Freepik AI asset research (Flux Pro + real photographer vocab) |
| 9 | Measurement foundation + creative intel + lifecycle + retention engine (MER vs platform ROAS, attribution, SEO publish loop) |
| 10 | Customer intelligence: RFM segmentation (absolute thresholds, not quintiles), VOC theme miner, unified customer profile API |
| 11 | Bidirectional Shopify↔Supabase sync: real-time product/customer webhooks + daily reconcile crons. Centralized `src/core/shopify_sync.py` shared by webhooks, crons, and manual backfill scripts |
| 11.5 | Reverse-sync: Meta ad status (pause/resume in Ads Manager mirrors into Supabase daily), Shopify blog publish status (newly-published detection + Slack celebration) |
| 12 | Agent ownership: tiered approval (AUTO/REVIEW/ESCALATE), per-agent dashboards, agent-owned KPIs (MER, repeat rate, resolution hours, net margin, on-time ship), weekly Claude-authored retros, approval feedback loop |
| 13 | Agent intelligence (research-validated): llm-wiki entity memory (customers/products/seasonal, file-based not vector), program-verified outcomes (closed taxonomy, SendGrid webhook + SQL verifiers, never LLM-judged), cross-model skeptic (GPT-4o-mini reviews Claude with asymmetric rubric +5/−10), agent rolling self-memory (compaction of audit_log + observations so agents read distilled view not raw rows). Prereqs: confidence defaults to `unknown` not `high`, marketing strategy moved from prompt to `get_current_strategy` tool, context assembler per-agent slices, heartbeat reframed neutral |

## Shopify Theme Development

**Theme directory:** `shopify-theme/`
**Live theme ID:** 159721455874 (Pinaka Dawn)

### Push commands
```bash
# Always include settings_data.json to force CDN recompile
shopify theme push --store pinaka-jewellery --theme 159721455874 \
  --only 'assets/pinaka-custom.css' --only 'config/settings_data.json' \
  --allow-live --nodelete

# NEVER use --only without --nodelete (it deletes unlisted files!)
```

### Key rules
- **Always run `shopify theme push` from INSIDE `shopify-theme/` directory.** Running from repo root with `cd shopify-theme && ...` inline can silently no-op the `--only` file paths. Learned from a 30-minute debug session on 2026-04-10.
- **Always push `config/settings_data.json`** alongside any theme changes to force immediate CDN recompilation. Without it, changes take 15-30+ minutes to propagate.
- **Section render cache is sticky.** Even pushing `<h1>TEST CACHE BUST</h1>` to a section file can be served stale for minutes. If pushes don't appear live: (1) edit `templates/index.json` content, (2) edit `settings_data.json` by 1 byte, (3) have user click Save in Shopify admin Theme Editor.
- **Always use `--nodelete`** with `--only` flag. Without it, Shopify deletes all remote files not matching the filter.
- **Shopify's `cormorant_n4` is "Cormorant", not "Cormorant Garamond".** Different typeface. We load Cormorant Garamond from Google Fonts and override with `font-family: 'Cormorant Garamond', serif !important`.
- **Use CSS variables** from `pinaka-custom.css` (`--pinaka-accent`, `--pinaka-charcoal`, etc.) not hardcoded hex in section files.
- **Dark mode requires explicit overrides** for every custom section. Dawn only handles its own components.
- **Design reference:** `file:///private/tmp/design-consultation-preview-1775112210.html` — the source of truth for fonts, sizes, colors, spacing.

### Product visibility (CRITICAL)
Shopify requires BOTH fields set for a product to appear on the storefront:
- `status: active` — means the product is not archived/draft
- `published_at` set to a timestamp — means it's published to the Online Store sales channel

**Setting `status` alone is NOT enough.** A product with `status: active, published_at: null` will NOT appear on pinakajewellery.com. The dashboard edit flow auto-sets `published_at` when status flips to active. Pipeline publish creates as draft (intentional — review before going live).

### Shopify Storefront MCP
- Endpoint: `https://pinaka-jewellery.myshopify.com/api/mcp`
- Tools are implicitly versioned — names can change without notice. As of 2026-04-10:
  - `search_catalog` (NOT `search_shop_catalog` — renamed)
  - `get_cart`, `update_cart`
  - `search_shop_policies_and_faqs`
  - `get_product_details`
- **Always log warnings on MCP errors.** Silent exception handlers hid a product search failure for weeks until a customer tested the concierge. Check `result.isError` and `result.content[0].text` for "Tool not found" messages.
- Response format for search_catalog: `price_range.min` is now `{"amount": 510000, "currency": "USD"}` (amount in cents). `media` array replaces flat `image_url` field.

### AI Image Generation (Freepik)
- API key: `FREEPIK_API_KEY` on Railway
- **Best realism: Flux Pro v1.1** (`/v1/ai/text-to-image/flux-pro-v1-1`) — the 2026 winner. Use this for product photography.
- **Video: Kling o1 Pro** (`/v1/ai/image-to-video/kling-o1-pro`) — ~$1.12 per 10-sec video, supports `first_frame` base64 input
- **AVOID these AI-buzzword triggers in prompts:** cinematic, ultra-detailed, hyper-realistic, 8K, masterpiece, stunning, magnific, magnificent, dramatic lighting, volumetric lighting, vibrant colors, perfect, flawless, pristine, photorealistic (ironically), sharp focus, beautiful, luxurious
- **USE real photography vocabulary:** Hasselblad X2D 100C, Canon EOS R5, Phase One IQ4, Nikon Z9, 100mm/105mm/120mm macro lens, f/8-f/11, ISO 100, 1/160s, tripod mounted, Profoto D2 octabox, Kodak Portra 400, Fuji Pro 400H, unretouched raw file, editorial catalog reference shot
- Free trial caps at ~30 successful calls. Paid plan required for production use.

### Font stack (from design system)
| Use | Font | Size | Weight |
|-----|------|------|--------|
| Headings | Cormorant Garamond (Google Fonts) | 36px | 400 |
| Body | DM Sans (Shopify settings) | 16px | 400 |
| Prices/data | Geist Mono (Google Fonts) | 26px/12px | 500 |
| Labels | DM Sans | 12px | 600 |
| Pills | DM Sans | 14px | 400 |

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

### Available Skills

- `/office-hours` — Brainstorm a new idea
- `/plan-ceo-review` — Review a plan (strategy)
- `/plan-eng-review` — Review a plan (architecture)
- `/plan-design-review` — Review a plan (design)
- `/design-consultation` — Create a design system
- `/review` — Code review before merge
- `/ship` — Create PR / deploy
- `/land-and-deploy` — Land and deploy changes
- `/canary` — Canary deployment
- `/benchmark` — Performance benchmarking
- `/browse` — Web browsing and testing
- `/qa` — QA testing
- `/qa-only` — QA testing (test-only mode)
- `/design-review` — Visual design audit
- `/setup-browser-cookies` — Set up browser cookies for testing
- `/setup-deploy` — Set up deployment config
- `/retro` — Weekly retrospective
- `/investigate` — Debug errors
- `/document-release` — Post-ship doc updates
- `/codex` — Adversarial code review / second opinion
- `/cso` — Chief Security Officer review
- `/autoplan` — Auto-generate implementation plan
- `/careful` — Maximum safety mode
- `/freeze` — Scope edits to one module/directory
- `/guard` — Destructive warnings + edit restrictions
- `/unfreeze` — Remove edit restrictions
- `/gstack-upgrade` — Upgrade gstack to latest version

## Before You Start (read every session)

1. **Read MEMORY.md** (project root) — contains pointers to persistent memory files with user profile, project state, business details, testing conventions, and external references. Memory files live in `~/.claude/projects/-Users-jaitulbharodiya-Documents-GitHub-pinaka_agents/memory/`.
2. **Read RETRO.md** — captures what shipped, what worked, what hurt, and lessons learned from every push to main. Learn from past mistakes before repeating them.
3. **Read TODO.md** — know what's done, what's next, and what's blocked.
4. **Skim the "Agent System Design Rules" section above** — 12 non-negotiable rules the research + incidents validated. Break one = bug.

## After Every Push to Main

1. **Update RETRO.md** — add a new entry: what shipped, what went well, what was painful, lessons learned. Keep it short (3-5 bullets).
2. **Update TODO.md** — check off completed items, add new tasks discovered during the work.
3. **Update MEMORY.md** if any new persistent knowledge was learned (user preferences, project decisions, external references).

## Auto Learning / Reinforced Learning

This project follows a reinforced learning loop. Every session should be smarter than the last:

- **Before work:** Read RETRO.md for lessons, MEMORY.md for context, TODO.md for priorities.
- **During work:** When something unexpected happens (a tool fails, an approach doesn't work, user corrects you), note it mentally for the retro.
- **After work:** Write what you learned to RETRO.md so the next session benefits. Update memory if the lesson is durable (not just this task).
- **Pattern recognition:** If the same lesson appears in RETRO.md more than twice, it should become a rule in CLAUDE.md (this file) or a memory file.
- **Never repeat a mistake documented in RETRO.md.** That's the whole point.

Examples of things to capture:
- "User prefers simplicity over completeness" → feedback memory
- "OpenAI API key not configured for gstack design tool" → project memory
- "Square images prevent oversized cards in Shopify grids" → retro lesson
- "Always check Railway vars before asking user for secrets" → CLAUDE.md rule (already here)

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match DESIGN.md.
