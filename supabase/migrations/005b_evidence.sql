-- Phase 4 Sprint 2: Chargeback evidence tracking
ALTER TABLE orders ADD COLUMN IF NOT EXISTS evidence_collected_at TIMESTAMPTZ;
