-- Phase 13.3 — cross-model skeptic reviews
--
-- Karpathy's LLM-Council finding: "Models are surprisingly willing to
-- select another LLM's response as superior to their own." Cross-model
-- critique carries signal. Same-model self-critique (Claude-reviews-Claude)
-- does not (Kamoi TACL 2024).
--
-- This table logs every cross-model review so we can measure:
--   • How often the skeptic is invoked (gated on drafter confidence)
--   • Its pass/revise/block distribution
--   • Whether the founder overrides a block (negative signal on the
--     skeptic's calibration — "it was too harsh here").
--
-- The asymmetric rubric is in src/agents/skeptic.py: +5 for catching a
-- real issue, −10 for rejecting a clean draft. This is the "caution"
-- lever that prevents the critic from rejecting everything to look busy.

CREATE TABLE IF NOT EXISTS skeptic_reviews (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    drafter_model TEXT NOT NULL,       -- e.g. 'claude-sonnet-4-5-20250929'
    reviewer_model TEXT NOT NULL,      -- e.g. 'gpt-4o-2024-11-20'
    action_type TEXT NOT NULL,         -- 'customer_response', 'cart_recovery', etc.
    entity_type TEXT,
    entity_id TEXT,
    draft_text TEXT NOT NULL,
    context_snippet TEXT,              -- short description of the situation
    verdict TEXT NOT NULL CHECK (verdict IN ('pass','revise','block')),
    findings JSONB NOT NULL DEFAULT '[]'::JSONB,
    score NUMERIC,                     -- -10..+5, reviewer's self-reported
    tokens_reviewer INT,
    overridden_by_founder BOOLEAN NOT NULL DEFAULT false,
    override_reason TEXT,
    override_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_skeptic_reviews_verdict
    ON skeptic_reviews (verdict, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skeptic_reviews_recent
    ON skeptic_reviews (created_at DESC);
