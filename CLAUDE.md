# CLAUDE.md

## Project Overview

Pinaka Agents is an AI-powered autonomous operations system for **Pinaka Jewellery**, a premium handcrafted diamond tennis bracelet DTC brand on Shopify. The system handles the full e-commerce lifecycle: orders, shipping, customer service, finance, marketing, and product management, with human-in-the-loop Slack approvals.

**Stack:** Python 3.12 + FastAPI + Supabase (PostgreSQL) + Railway
**AI:** Claude Sonnet 4 (drafts, classification) + OpenAI text-embedding-3-small (RAG)
**Integrations:** Shopify, SendGrid, Slack, ShipStation, Meta Ads/CAPI, Google Ads/Merchant

## Architecture

```
Shopify Webhooks → FastAPI → Supabase → AI Processing → Slack Approval → Action (email/API)
                                                                        ↓
Cron Jobs (cron-job.org) → FastAPI endpoints → Business Logic → Slack/Email/DB
```

**8 modules** in `src/`:
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

Supabase PostgreSQL. Migrations in `supabase/migrations/` (001-006b).

**Core tables:** orders, customers, messages, daily_stats, products, listing_drafts, refunds, voice_examples, review_requests

## Development Commands

```bash
# Run tests (126 total, all passing)
.venv/bin/pytest tests/ -v

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

## Slack Approval Pattern

All customer-facing actions go through Slack approval:
1. AI drafts message/email
2. Posts to Slack with "Approve" / "Reject" buttons
3. Founder reviews and clicks
4. System sends or skips

15 Slack action types defined. Handler at `POST /webhook/slack`.

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

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match DESIGN.md.
