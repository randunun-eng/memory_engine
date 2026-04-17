-- ========================================================================
-- Migration 006: Observability + Prompt Shadow Harness (Phase 6)
-- Created: 2026-04-16
--
-- Adds tables for:
--   1. retrieval_traces — every recall emits a trace for consolidation
--      reinforcement (rule 7: retrieval never writes synchronously).
--   2. prompt_shadow_logs — per-execution comparison records when a shadow
--      prompt runs alongside an active prompt.
--   3. prompt_comparison_daily — aggregated metrics from shadow logs.
--   4. backup_status — last successful backup timestamp per persona.
-- ========================================================================

-- Retrieval traces: asynchronous record of every recall query.
-- Emitted by the retrieval layer, consumed by the consolidator for
-- LTP-style reinforcement of frequently-accessed neurons.
CREATE TABLE retrieval_traces (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  query_hash      TEXT NOT NULL,        -- hash of query + lens for dedup analysis
  lens            TEXT NOT NULL,        -- 'auto', 'self', 'counterparty:X', 'domain'
  result_neuron_ids TEXT NOT NULL,      -- JSON array of neuron ids returned
  latency_ms      INTEGER NOT NULL,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),
  consumed_at     TEXT                  -- set by consolidator when processed
);

CREATE INDEX ix_retrieval_traces_unconsumed
  ON retrieval_traces(persona_id, recorded_at)
  WHERE consumed_at IS NULL;

-- Prompt shadow execution logs: when a shadow runs alongside active.
-- Both outputs are captured; the active one is used; shadow is for comparison.
CREATE TABLE prompt_shadow_logs (
  id                INTEGER PRIMARY KEY,
  persona_id        INTEGER NOT NULL REFERENCES personas(id),
  site              TEXT NOT NULL,                  -- 'extract_entities', 'grounding_judge', ...
  active_template_id INTEGER NOT NULL REFERENCES prompt_templates(id),
  shadow_template_id INTEGER NOT NULL REFERENCES prompt_templates(id),
  input_hash        TEXT NOT NULL,                  -- hash of the prompt inputs for dedup
  active_output     TEXT NOT NULL,                  -- JSON of active prompt result
  shadow_output     TEXT NOT NULL,                  -- JSON of shadow prompt result
  active_latency_ms INTEGER NOT NULL,
  shadow_latency_ms INTEGER NOT NULL,
  active_cost_usd   REAL NOT NULL DEFAULT 0,
  shadow_cost_usd   REAL NOT NULL DEFAULT 0,
  recorded_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_prompt_shadow_logs_site_recent
  ON prompt_shadow_logs(site, recorded_at);

-- Daily aggregated comparison metrics.
-- Populated by a batch job that reads yesterday's shadow logs and computes
-- per-site metrics (grounding pass rate delta, cost delta, etc.).
CREATE TABLE prompt_comparison_daily (
  id                INTEGER PRIMARY KEY,
  day               TEXT NOT NULL,                  -- 'YYYY-MM-DD'
  site              TEXT NOT NULL,
  active_template_id INTEGER NOT NULL REFERENCES prompt_templates(id),
  shadow_template_id INTEGER NOT NULL REFERENCES prompt_templates(id),
  sample_count      INTEGER NOT NULL,
  metrics_json      TEXT NOT NULL,                  -- JSON of computed metrics
  computed_at       TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (day, site, active_template_id, shadow_template_id)
);

CREATE INDEX ix_prompt_comparison_day
  ON prompt_comparison_daily(day, site);

-- Backup status: one row per persona, updated by bin/backup.sh on success.
-- Monitored by the BackupStale alert (runbook: backup_stale.md).
CREATE TABLE backup_status (
  persona_id        INTEGER PRIMARY KEY REFERENCES personas(id),
  last_success_at   TEXT NOT NULL,
  last_artifact     TEXT NOT NULL,
  last_size_bytes   INTEGER NOT NULL,
  last_destination  TEXT NOT NULL
);
