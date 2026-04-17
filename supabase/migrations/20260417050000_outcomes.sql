-- Phase 13.1 — program-verified outcomes
--
-- Closes the feedback loop for every agent action: did it work?
-- The goal is "signals that a program can verify, not signals an LLM
-- decides." So every row here is written by either (a) an external
-- webhook (SendGrid events), (b) a scheduled SQL check, or (c) a
-- direct in-app hook. An agent never writes here on its own behalf.
--
-- Row shape:
--   audit_log_id      — link back to the agent run that caused the action
--   agent_name        — which of the 5 agents owns this outcome
--   action_type       — mirrors auto_sent_actions.action_type where possible
--   entity_type/id    — what the action was performed on
--   outcome_type      — deterministic event name, see taxonomy below
--   outcome_value     — structured payload (counts, ms, email address, etc.)
--   source            — where the row came from (sendgrid_webhook,
--                       verify_cron, internal_hook)
--   idempotency_key   — prevents double-counting SendGrid retries
--
-- Taxonomy (extend only when a new deterministic signal is identified):
--   email_delivered        SendGrid delivered event
--   email_opened           SendGrid open event (first time only)
--   email_clicked          SendGrid click event
--   email_bounced          SendGrid bounce event
--   email_replied_48h      customer sent a new message within 48h of our send
--   order_shipped_on_time  orders.shipped_at within 15 business days of created_at
--   order_shipped_late     shipped but > 15 business days
--   order_delivered        orders.delivered_at NOT NULL
--   customer_repurchase_30d  retention email sent; new order within 30d
--   refund_issued          refund row created after we interacted with the order

CREATE TABLE IF NOT EXISTS outcomes (
    id BIGSERIAL PRIMARY KEY,
    audit_log_id TEXT,
    agent_name TEXT NOT NULL,
    action_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    outcome_type TEXT NOT NULL,
    outcome_value JSONB NOT NULL DEFAULT '{}'::JSONB,
    source TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    fired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_agent_fired
    ON outcomes (agent_name, fired_at DESC);

CREATE INDEX IF NOT EXISTS idx_outcomes_type_fired
    ON outcomes (outcome_type, fired_at DESC);

CREATE INDEX IF NOT EXISTS idx_outcomes_entity
    ON outcomes (entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_outcomes_audit
    ON outcomes (audit_log_id)
    WHERE audit_log_id IS NOT NULL;
