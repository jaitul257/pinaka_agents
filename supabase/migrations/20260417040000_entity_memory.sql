-- Phase 13.2 — file-based entity memory
--
-- Karpathy llm-wiki pattern applied to Pinaka's domain objects:
--
--   raw (immutable)     →     LLM-compiled wiki
--   ─────────────             ─────────────────
--   orders / messages         entity_memory rows, 1 per (type, id)
--   products / metrics        compiled from raw, ~500-800 words each
--   daily_stats by month      agents never see the raw during reasoning
--
-- The wiki is what agents read. The raw is what the compiler reads. This
-- keeps agent context small and relevant without stuffing audit logs or
-- order history into every prompt.
--
-- sample_count + source_through are a cheap staleness check: if the
-- underlying raw data has moved on meaningfully since compile time, the
-- nightly cron recompiles. No vectors; retrieval is a keyed SELECT.

CREATE TABLE IF NOT EXISTS entity_memory (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('customer', 'product', 'seasonal')),
    entity_id TEXT NOT NULL,        -- customer_id, sku, or YYYY-MM for seasonal
    content TEXT NOT NULL,           -- markdown, ~500-800 words
    sample_count INTEGER NOT NULL DEFAULT 0,  -- rows of raw data that went into this compile
    source_through TIMESTAMPTZ,      -- max(created_at) of raw data seen at compile time
    compiled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_memory_lookup
    ON entity_memory (entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_memory_stale
    ON entity_memory (entity_type, compiled_at);
