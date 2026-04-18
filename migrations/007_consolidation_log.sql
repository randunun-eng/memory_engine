-- Migration 007: consolidation log
--
-- Additive migration. Adds a durable record of which events have been
-- through the extraction pass, so events can't re-enter extraction
-- after working_memory prunes them by activation decay.
--
-- The prior behaviour (_find_unconsolidated_events LEFT JOIN working_memory)
-- was wrong under Phase 7 load: working_memory is a ring buffer with
-- activation-based pruning; once an event's entry was pruned, it looked
-- "unconsolidated" on the next tick and the extractor ran again. See DRIFT
-- `consolidator-duplicate-extraction-loop`.
--
-- The log is append-only and carries (persona_id, event_id). One row per
-- event-per-persona. `consolidated_at` is informational.

CREATE TABLE IF NOT EXISTS consolidation_log (
  id             INTEGER PRIMARY KEY,
  persona_id     INTEGER NOT NULL REFERENCES personas(id),
  event_id       INTEGER NOT NULL REFERENCES events(id),
  consolidated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (persona_id, event_id)
);

CREATE INDEX IF NOT EXISTS ix_consolidation_log_persona_event
  ON consolidation_log(persona_id, event_id);

-- Backfill: every event currently referenced by an active neuron's
-- source_event_ids has, by definition, already been consolidated. Mark them
-- so this migration doesn't re-trigger extraction on every event in the log
-- when the updated consolidator first runs. Uses SQLite's json_each to
-- expand the JSON array.
INSERT OR IGNORE INTO consolidation_log (persona_id, event_id)
SELECT DISTINCT n.persona_id, CAST(je.value AS INTEGER)
FROM neurons n, json_each(n.source_event_ids) je
WHERE n.superseded_at IS NULL;
