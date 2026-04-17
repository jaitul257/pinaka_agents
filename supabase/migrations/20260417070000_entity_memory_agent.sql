-- Phase 13.4 — extend entity_memory to cover agents themselves.
--
-- The llm-wiki pattern applied to each agent's own 7-day history. Raw
-- audit_log + observations + auto_sent_actions + outcomes become one
-- compiled markdown summary per agent, refreshed nightly. Agents pull
-- this when reasoning instead of walking raw tables that grow forever.
--
-- Drop the entity_type CHECK rather than extend it: Python
-- (src/agents/memory.py SUPPORTED_TYPES) is the enforcement point and
-- keeping the constraint list in two places invites drift.

ALTER TABLE entity_memory DROP CONSTRAINT IF EXISTS entity_memory_entity_type_check;
