-- Migration 009: per-counterparty episodes
--
-- Additive migration. `episodes` was defined in 002_consolidation.sql
-- scoped only to `persona_id` — but real conversations happen per
-- counterparty (Harsha, Babi, each contact has their own thread with
-- its own episode boundaries). Without a counterparty scope, any
-- episode summary would pollute across chats, violating rule 12.
--
-- Adds counterparty_id (nullable — NULL means persona-wide / domain)
-- plus a partial index for the lens-scoped lookup pattern that
-- /v1/chat_context will do.

ALTER TABLE episodes ADD COLUMN counterparty_id INTEGER REFERENCES counterparties(id);

CREATE INDEX IF NOT EXISTS ix_episodes_counterparty
  ON episodes(counterparty_id, created_at DESC)
  WHERE counterparty_id IS NOT NULL;
