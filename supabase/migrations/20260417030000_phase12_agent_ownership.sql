-- Phase 12: agent ownership layer
--
-- Converts agents from "drafters who wait for you" into owners with:
--   • a north-star KPI (agent_kpis)
--   • weekly retros (agent_retros)
--   • captured founder edits for style learning (approval_feedback)
--   • tiered approvals — AUTO actions are logged here so founder can
--     review or undo after the fact (auto_sent_actions)

-- ── agent_kpis ────────────────────────────────────────────────────
-- One row per (agent_name, date). Populated by /cron/compute-agent-kpis.
-- value is the raw metric; trend_7d / trend_30d are % change vs that lookback.

CREATE TABLE IF NOT EXISTS agent_kpis (
    id BIGSERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    kpi_name TEXT NOT NULL,  -- e.g. "mer", "repeat_rate", "resolution_hours"
    value NUMERIC NOT NULL,
    trend_7d NUMERIC,  -- percentage change over last 7d (null if not enough data)
    trend_30d NUMERIC,
    computed_for_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_name, kpi_name, computed_for_date)
);

CREATE INDEX IF NOT EXISTS idx_agent_kpis_agent_date
    ON agent_kpis (agent_name, computed_for_date DESC);


-- ── agent_retros ──────────────────────────────────────────────────
-- Monday 8 AM ET cron generates a self-review for each agent.

CREATE TABLE IF NOT EXISTS agent_retros (
    id BIGSERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    week_start DATE NOT NULL,  -- Monday of the week being reviewed
    summary_text TEXT NOT NULL,  -- Claude-written 2-para narrative
    kpi_snapshot JSONB NOT NULL DEFAULT '{}'::JSONB,  -- {kpi_name: value, trend_7d}
    actions_summary JSONB NOT NULL DEFAULT '{}'::JSONB,  -- {tier: count, tool_name: count}
    needs_from_founder TEXT,  -- "what I need from you" — nullable when agent is unblocked
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_name, week_start)
);

CREATE INDEX IF NOT EXISTS idx_agent_retros_week
    ON agent_retros (week_start DESC);


-- ── approval_feedback ─────────────────────────────────────────────
-- When founder edits a Claude draft before approving, we store the diff
-- so we can feed a "founder_style" summary back into the agent prompt.

CREATE TABLE IF NOT EXISTS approval_feedback (
    id BIGSERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,  -- e.g. "customer_response", "lifecycle_anniversary"
    original_text TEXT NOT NULL,
    edited_text TEXT NOT NULL,
    context JSONB NOT NULL DEFAULT '{}'::JSONB,  -- e.g. customer tier, order id
    incorporated BOOLEAN NOT NULL DEFAULT false,  -- flipped once rolled into prompt
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_approval_feedback_trigger
    ON approval_feedback (agent_name, trigger_type, created_at DESC)
    WHERE incorporated = false;


-- ── auto_sent_actions ─────────────────────────────────────────────
-- AUTO-tier actions bypass Slack. We still log them so founder can
-- review in /dashboard/agents and flag any mistakes.

CREATE TABLE IF NOT EXISTS auto_sent_actions (
    id BIGSERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    action_type TEXT NOT NULL,  -- e.g. "crafting_update_email", "welcome_day_2"
    entity_type TEXT,   -- e.g. "order", "customer"
    entity_id TEXT,     -- id of the thing acted upon
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,  -- what was sent (email body, etc.)
    flagged BOOLEAN NOT NULL DEFAULT false,  -- founder clicked "this was wrong"
    flag_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auto_sent_recent
    ON auto_sent_actions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auto_sent_flagged
    ON auto_sent_actions (flagged)
    WHERE flagged = true;
