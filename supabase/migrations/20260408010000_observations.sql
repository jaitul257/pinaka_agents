-- Observations table: the agent awareness layer.
-- Every webhook, cron, and agent writes business-level observations here.
-- The heartbeat scans this table to find things that need attention.

CREATE TABLE IF NOT EXISTS observations (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL,              -- 'webhook:order', 'cron:roas', 'agent:order_ops', etc.
    category        TEXT NOT NULL,              -- 'order', 'customer', 'shipping', 'marketing', 'finance', 'support'
    severity        TEXT NOT NULL DEFAULT 'info', -- 'info', 'warning', 'critical'
    summary         TEXT NOT NULL,              -- Human-readable: "Order #1234 stuck in paid for 3 days"
    entity_type     TEXT,                       -- 'order', 'customer', 'cart', etc.
    entity_id       TEXT,                       -- Shopify order ID, customer email, etc.
    data            JSONB DEFAULT '{}'::JSONB,  -- Structured data for the heartbeat to reason about
    acted_on        BOOLEAN NOT NULL DEFAULT FALSE,  -- True once heartbeat has processed it
    acted_at        TIMESTAMPTZ,
    action_taken    TEXT                        -- What the heartbeat decided to do
);

-- Heartbeat scans unacted observations by severity
CREATE INDEX IF NOT EXISTS idx_obs_unacted ON observations (acted_on, severity, created_at DESC) WHERE acted_on = FALSE;

-- Look up observations by entity
CREATE INDEX IF NOT EXISTS idx_obs_entity ON observations (entity_type, entity_id, created_at DESC);

-- Prune old observations (keep 30 days)
CREATE INDEX IF NOT EXISTS idx_obs_created ON observations (created_at);


-- Heartbeat state: tracks what the heartbeat has seen and when
CREATE TABLE IF NOT EXISTS heartbeat_state (
    key             TEXT PRIMARY KEY,
    value           JSONB NOT NULL DEFAULT '{}'::JSONB,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed initial state
INSERT INTO heartbeat_state (key, value) VALUES
    ('last_run', '{"timestamp": null, "observations_processed": 0}'::JSONB),
    ('counters', '{"total_beats": 0, "beats_with_action": 0, "total_observations": 0}'::JSONB)
ON CONFLICT (key) DO NOTHING;
