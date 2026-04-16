-- ========================================================================
-- Migration 003: Healing and invariant infrastructure
-- Phase: 3 (Invariants + Healer)
-- Created: 2026-04-16
--
-- Adds healing_log for tracking invariant check results, violations,
-- repairs, and escalations. The healer loop writes here; operators
-- query it for diagnostics.
--
-- Additive only (rule from CLAUDE.md §4.8). No drops or renames.
-- ========================================================================

-- Healing log: records every invariant check outcome.
-- Critical violations trigger system halt; warnings log for review.
CREATE TABLE healing_log (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER,
  invariant_name  TEXT NOT NULL,
  severity        TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
  status          TEXT NOT NULL CHECK (status IN ('detected', 'repaired', 'quarantined', 'escalated')),
  details         TEXT NOT NULL,
  detected_at     TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at     TEXT
);

CREATE INDEX ix_healing_unresolved
  ON healing_log(persona_id, severity)
  WHERE resolved_at IS NULL;

CREATE INDEX ix_healing_severity
  ON healing_log(severity, detected_at DESC);
