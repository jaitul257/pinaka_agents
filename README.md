# Pinaka Agents

AI-powered e-commerce platform for Diamond Tennis Bracelets on Etsy. Six autonomous agent modules handle product intelligence, listing generation, shipping/fraud detection, marketing analytics, finance tracking, and customer service — all coordinated through Slack and a Streamlit dashboard.

## Architecture

```
src/
├── core/           # Shared infra: settings, rate limiter, Etsy client, DB, Slack
├── product/        # Module 1: Product schema + ChromaDB RAG embeddings
├── listings/       # Module 2: Claude-powered listing generator
├── shipping/       # Module 3: Fraud detection + insurance validation
├── marketing/      # Module 4: ROAS tracking + budget recommendations
├── finance/        # Module 5: Per-order P&L, Etsy fee calculator, weekly reports
├── customer/       # Module 6: Message classifier + AI draft responses
├── api/            # FastAPI: health, 8 cron endpoints, Etsy/Slack webhooks
└── dashboard/      # Streamlit: 5-page monitoring dashboard
```

## Tech Stack

- **Python 3.12**, FastAPI, Streamlit
- **Claude Sonnet 4** (listing generation, message classification, response drafting)
- **OpenAI text-embedding-3-small** (product RAG via ChromaDB)
- **Supabase** (PostgreSQL: orders, customers, messages, daily stats)
- **Slack SDK** (Block Kit interactive messages, approval workflows)
- **Railway** (deployment + native cron scheduling)

## Prerequisites

- Python 3.11+
- Etsy API v3 access (apply at etsy.com/developers)
- Anthropic API key
- OpenAI API key
- Supabase project
- Slack workspace with a bot app

## Installation

```bash
# Clone
git clone <your-repo-url>
cd pinaka_agents

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

## Configuration

```bash
cp .env.example .env
```

Fill in all values in `.env`. Required:

| Variable | Description |
|----------|-------------|
| `ETSY_API_KEY` | Etsy v3 keystring |
| `ETSY_SHARED_SECRET` | Etsy v3 shared secret |
| `ETSY_SHOP_ID` | Your Etsy shop ID |
| `ETSY_ACCESS_TOKEN` | OAuth2 access token |
| `ETSY_REFRESH_TOKEN` | OAuth2 refresh token |
| `ANTHROPIC_API_KEY` | Claude API key |
| `OPENAI_API_KEY` | For embeddings |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service role key |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token |
| `SLACK_CHANNEL_ID` | Channel for notifications |
| `CRON_SECRET` | Secret for cron endpoint auth |
| `DASHBOARD_PASSWORD` | Streamlit dashboard login |

Generate a cron secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Database Setup

Run the migration in your Supabase SQL editor:

```bash
# File: supabase/migrations/001_initial_schema.sql
```

This creates 5 tables: `orders`, `customers`, `messages`, `daily_stats`, `review_requests` with indexes, RLS policies, and auto-updating timestamps.

## Running Locally

```bash
# API server (port 8000)
uvicorn src.api.app:app --reload --port 8000

# Dashboard (port 8501, separate terminal)
streamlit run src/dashboard/app.py

# Both run independently — the dashboard reads from the same Supabase DB
```

Verify the API is running:

```bash
curl http://localhost:8000/health
```

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Unit tests only
python -m pytest tests/unit/ -v

# Integration tests only
python -m pytest tests/integration/ -v
```

## Cron Schedule

All cron endpoints require the `X-Cron-Secret` header. Trigger manually:

```bash
curl -X POST http://localhost:8000/cron/morning-digest \
  -H "X-Cron-Secret: your-secret-here"
```

| Endpoint | Schedule | What it does |
|----------|----------|--------------|
| `/cron/check-orders` | Every 10 min | Poll Etsy for new orders, run fraud checks |
| `/cron/conversations` | Every 10 min | Poll Etsy messages, classify, draft AI responses |
| `/cron/daily-orders` | 10 PM UTC | Sync orders, calculate per-order profit |
| `/cron/daily-stats` | 11 PM UTC | Aggregate daily marketing stats |
| `/cron/morning-digest` | 1 PM UTC | Slack digest: yesterday's revenue + pending messages |
| `/cron/weekly-roas` | Mon 2 PM UTC | ROAS report with budget recommendation |
| `/cron/weekly-finance` | Mon 2 PM UTC | Weekly P&L summary |
| `/cron/weekly-rollup` | Mon 2 PM UTC | Weekly rollup to Slack |

## Deployment

See `deploy/DEPLOY.md` for full Railway + Supabase deployment instructions.

Quick version:

1. Push to GitHub
2. Connect repo to Railway
3. Add env vars in Railway dashboard
4. Run `supabase/migrations/001_initial_schema.sql` in Supabase
5. Set up Railway cron services per `deploy/railway-crons.json`
6. Set Slack interactivity URL to `https://your-app.railway.app/webhook/slack`

## Project Structure

```
pinaka_agents/
├── src/
│   ├── core/
│   │   ├── settings.py          # Pydantic Settings, all config
│   │   ├── rate_limiter.py      # Token bucket HTTP client
│   │   ├── etsy_client.py       # Etsy v3 API with OAuth2 refresh
│   │   ├── database.py          # Supabase typed operations
│   │   └── slack.py             # Block Kit message templates
│   ├── product/
│   │   ├── schema.py            # Pydantic product models
│   │   └── embeddings.py        # ChromaDB RAG
│   ├── listings/
│   │   └── generator.py         # Claude listing generator
│   ├── shipping/
│   │   └── processor.py         # Fraud detection + insurance
│   ├── marketing/
│   │   └── ads.py               # ROAS calculator + budget recs
│   ├── finance/
│   │   └── calculator.py        # Etsy fees, per-order P&L
│   ├── customer/
│   │   └── classifier.py        # Message classification + AI drafts
│   ├── api/
│   │   └── app.py               # FastAPI: crons, webhooks, health
│   └── dashboard/
│       └── app.py               # Streamlit 5-page dashboard
├── tests/
│   ├── unit/                    # 27 unit tests
│   └── integration/             # 16 API integration tests
├── data/products/               # Product JSON files
├── supabase/migrations/         # Database schema
├── deploy/                      # Railway config + deploy guide
├── DESIGN.md                    # Design system (colors, fonts, spacing)
├── .env.example                 # Environment variable template
├── pyproject.toml               # Dependencies + tool config
└── railway.toml                 # Railway deployment config
```
