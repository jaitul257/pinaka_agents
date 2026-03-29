# Deployment Guide — Pinaka Agents

## Prerequisites

1. **Supabase project** created at supabase.com
2. **Railway account** at railway.app
3. **Etsy API v3 access** (apply at etsy.com/developers)
4. **Anthropic API key** for Claude
5. **OpenAI API key** for embeddings
6. **Slack bot** created at api.slack.com

## Step 1: Supabase Setup

1. Create a new Supabase project
2. Go to SQL Editor and run `supabase/migrations/001_initial_schema.sql`
3. Copy your project URL and `service_role` key (not the anon key)

## Step 2: Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

Required variables:
- `ETSY_API_KEY`, `ETSY_SHARED_SECRET`, `ETSY_SHOP_ID`
- `ETSY_ACCESS_TOKEN`, `ETSY_REFRESH_TOKEN` (from OAuth2 flow)
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`
- `CRON_SECRET` (generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- `DASHBOARD_PASSWORD`

## Step 3: Railway Deployment

1. Connect your GitHub repo to Railway
2. Railway auto-detects Python and uses `railway.toml` config
3. Add all env vars from `.env` to Railway's Variables tab
4. The web service starts automatically with the health check at `/health`

## Step 4: Railway Cron Jobs

Railway uses native cron services. For each cron in `deploy/railway-crons.json`:

1. Create a new "Cron Service" in your Railway project
2. Set the schedule from the JSON
3. Set the command to curl your web service:

```bash
curl -X POST https://YOUR-APP.railway.app/cron/ENDPOINT \
  -H "X-Cron-Secret: $CRON_SECRET"
```

Or use Railway's built-in HTTP cron feature if available.

### Cron Schedule Summary

| Job | Schedule | Description |
|-----|----------|-------------|
| check-orders | Every 10 min | Poll Etsy for new orders |
| conversations | Every 10 min | Poll Etsy messages |
| daily-orders | 10 PM UTC | Sync orders + financials |
| daily-stats | 11 PM UTC | Marketing stats |
| morning-digest | 1 PM UTC (8 AM IST) | Slack morning digest |
| weekly-roas | Mon 2 PM UTC | ROAS report |
| weekly-finance | Mon 2 PM UTC | P&L summary |
| weekly-rollup | Mon 2 PM UTC | Weekly Slack rollup |

## Step 5: Slack Setup

1. Create a Slack App at api.slack.com/apps
2. Add Bot Token Scopes: `chat:write`, `chat:update`
3. Install to workspace, copy Bot Token
4. Create a channel for Pinaka notifications, copy Channel ID
5. Set Interactivity URL to: `https://YOUR-APP.railway.app/webhook/slack`

## Step 6: Streamlit Dashboard

The dashboard runs separately (or on the same Railway project as a second service):

```bash
streamlit run src/dashboard/app.py --server.port 8501
```

For Railway, create a second service with:
```
startCommand: streamlit run src/dashboard/app.py --server.port $PORT --server.address 0.0.0.0
```

## Step 7: Verify

1. Check `/health` returns all modules OK
2. Trigger a test cron: `curl -X POST https://YOUR-APP.railway.app/cron/morning-digest -H "X-Cron-Secret: YOUR_SECRET"`
3. Verify Slack receives the morning digest
4. Open the Streamlit dashboard and log in

## Running Locally

```bash
# API server
uvicorn src.api.app:app --reload --port 8000

# Dashboard (separate terminal)
streamlit run src/dashboard/app.py

# Tests
python -m pytest tests/ -v
```
