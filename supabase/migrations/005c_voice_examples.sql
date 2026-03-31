-- Phase 4 Sprint 3: Voice learning examples table
CREATE TABLE IF NOT EXISTS voice_examples (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    original_draft TEXT NOT NULL,
    edited_draft TEXT NOT NULL,
    customer_message TEXT NOT NULL DEFAULT '',
    was_edited BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE voice_examples ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON voice_examples FOR ALL USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_voice_examples_category ON voice_examples(category);
