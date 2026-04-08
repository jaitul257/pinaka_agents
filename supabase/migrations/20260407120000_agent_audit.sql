-- Agent audit log for tracking all AI agent decisions
-- Every agent run records: tools called, policies applied, escalations, token usage

CREATE TABLE IF NOT EXISTS agent_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_name      TEXT NOT NULL,
    task_summary    TEXT NOT NULL,
    tool_calls      JSONB NOT NULL DEFAULT '[]'::JSONB,
    policy_decisions JSONB NOT NULL DEFAULT '[]'::JSONB,
    result          TEXT NOT NULL,           -- 'success' | 'failed'
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    escalated       BOOLEAN NOT NULL DEFAULT FALSE
);

-- Index for querying recent entries by agent
CREATE INDEX IF NOT EXISTS idx_agent_audit_agent_name ON agent_audit_log (agent_name, created_at DESC);

-- Index for token budget queries (sum tokens for today)
CREATE INDEX IF NOT EXISTS idx_agent_audit_created ON agent_audit_log (created_at);

-- Index for finding escalated entries
CREATE INDEX IF NOT EXISTS idx_agent_audit_escalated ON agent_audit_log (escalated) WHERE escalated = TRUE;
