-- ========================================================================
-- Migration 002: Consolidation tables
-- Phase: 2 (Consolidator + Grounding Gate)
-- Created: 2026-04-16
--
-- Adds working_memory ring buffer, quarantine for rejected neuron
-- candidates, episodes for conversation spans, and prompt_templates
-- for the policy plane's prompt versioning system.
--
-- Additive only (rule from CLAUDE.md §4.8). No drops or renames.
-- ========================================================================

-- Working memory: the ring buffer of recent events awaiting consolidation.
-- activation decays over time; the consolidator promotes high-activation
-- entries to episodic tier and prunes below threshold.
CREATE TABLE working_memory (
  id           INTEGER PRIMARY KEY,
  persona_id   INTEGER NOT NULL REFERENCES personas(id),
  event_id     INTEGER NOT NULL REFERENCES events(id),
  entered_at   TEXT NOT NULL DEFAULT (datetime('now')),
  activation   REAL NOT NULL DEFAULT 1.0
);

CREATE INDEX ix_working_memory_persona_activation
  ON working_memory(persona_id, activation DESC);

CREATE INDEX ix_working_memory_event
  ON working_memory(event_id);

-- Quarantine: rejected neuron candidates that failed the grounding gate.
-- Not silently dropped — surfaced in healer digest (Phase 3).
CREATE TABLE quarantine_neurons (
  id                INTEGER PRIMARY KEY,
  persona_id        INTEGER NOT NULL REFERENCES personas(id),
  candidate_json    TEXT NOT NULL,
  reason            TEXT NOT NULL,
  source_event_ids  TEXT NOT NULL,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at       TEXT,
  review_verdict    TEXT
);

CREATE INDEX ix_quarantine_persona_unreviewed
  ON quarantine_neurons(persona_id)
  WHERE reviewed_at IS NULL;

-- Episodes: contiguous spans of events (conversation sessions) with summaries.
-- Produced by the consolidator when a conversation boundary is detected.
CREATE TABLE episodes (
  id            INTEGER PRIMARY KEY,
  persona_id    INTEGER NOT NULL REFERENCES personas(id),
  start_event   INTEGER NOT NULL REFERENCES events(id),
  end_event     INTEGER NOT NULL REFERENCES events(id),
  summary       TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_episodes_persona
  ON episodes(persona_id);

-- Prompt templates: versioned prompt storage for the policy plane.
-- Only one active template per site at a time (enforced by partial unique index).
-- Shadow templates receive a fraction of traffic for A/B comparison.
CREATE TABLE prompt_templates (
  id                 INTEGER PRIMARY KEY,
  site               TEXT NOT NULL,
  version            TEXT NOT NULL,
  template_text      TEXT NOT NULL,
  parameters         TEXT NOT NULL,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  active             INTEGER NOT NULL DEFAULT 0,
  shadow             INTEGER NOT NULL DEFAULT 0,
  shadow_traffic_pct REAL NOT NULL DEFAULT 0,
  UNIQUE (site, version)
);

CREATE UNIQUE INDEX ix_prompt_templates_active
  ON prompt_templates(site)
  WHERE active = 1;
