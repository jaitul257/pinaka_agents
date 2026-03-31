-- Phase 4 Sprint 3: Reorder reminder tracking
ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_reorder_email_at TIMESTAMPTZ;
