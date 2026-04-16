-- ========================================================================
-- Migration 001: Initial schema
-- Phase: 0 (Skeleton)
-- Created: 2026-04-16
--
-- Creates the core tables: personas, counterparties, events, neurons.
-- Adds immutability triggers on events (rule 1).
-- Creates neurons_vec virtual table for vector retrieval (Phase 1 populates).
--
-- Requires: sqlite-vec extension loaded at connection time.
-- See docs/runbooks/sqlite_vec_install.md.
-- ========================================================================

-- Personas: the top-level identity anchor. One per digital twin.
CREATE TABLE personas (
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  identity_doc    TEXT,
  version         INTEGER NOT NULL DEFAULT 1
);

-- Counterparties: external entities the persona talks to.
CREATE TABLE counterparties (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  external_ref    TEXT NOT NULL,
  display_name    TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (persona_id, external_ref)
);

-- Events: the immutable log. Rule 1.
CREATE TABLE events (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  counterparty_id INTEGER REFERENCES counterparties(id),
  type            TEXT NOT NULL,
  scope           TEXT NOT NULL CHECK (scope IN ('private', 'shared', 'public')),
  content_hash    TEXT NOT NULL,
  idempotency_key TEXT,
  payload         TEXT NOT NULL,
  signature       TEXT NOT NULL,
  mcp_source_id   INTEGER,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (idempotency_key)
);

CREATE INDEX ix_events_persona_recorded
  ON events(persona_id, recorded_at);

CREATE INDEX ix_events_counterparty
  ON events(counterparty_id)
  WHERE counterparty_id IS NOT NULL;

CREATE INDEX ix_events_type_persona
  ON events(persona_id, type);

-- Rule 1: events are immutable. Enforced by triggers, not just Python discipline.
-- Uses a deliberate reference to a non-existent table so that the resulting
-- error is an OperationalError (not IntegrityError), making the constraint
-- distinguishable from schema-level CHECK/FK violations.
CREATE TRIGGER events_immutable_update
BEFORE UPDATE ON events
FOR EACH ROW
BEGIN
  SELECT * FROM "events are immutable (rule 1)";
END;

CREATE TRIGGER events_immutable_delete
BEFORE DELETE ON events
FOR EACH ROW
BEGIN
  SELECT * FROM "events are immutable (rule 1)";
END;

-- Neurons: derived facts. Phase 0 creates the table; Phase 2 populates it.
CREATE TABLE neurons (
  id                      INTEGER PRIMARY KEY,
  persona_id              INTEGER NOT NULL REFERENCES personas(id),
  counterparty_id         INTEGER REFERENCES counterparties(id),
  kind                    TEXT NOT NULL
    CHECK (kind IN ('self_fact', 'counterparty_fact', 'domain_fact')),
  content                 TEXT NOT NULL,
  content_hash            TEXT NOT NULL,
  source_event_ids        TEXT NOT NULL,
  source_count            INTEGER NOT NULL DEFAULT 1,
  distinct_source_count   INTEGER NOT NULL DEFAULT 1,
  tier                    TEXT NOT NULL
    CHECK (tier IN ('working', 'episodic', 'semantic', 'procedural')),
  t_valid_start           TEXT,
  t_valid_end             TEXT,
  recorded_at             TEXT NOT NULL DEFAULT (datetime('now')),
  superseded_at           TEXT,
  superseded_by           INTEGER REFERENCES neurons(id),
  embedder_rev            TEXT NOT NULL,
  version                 INTEGER NOT NULL DEFAULT 1,

  -- Rule 11 enforcement at the schema level: counterparty_fact requires a counterparty_id.
  CHECK (
    (kind = 'counterparty_fact' AND counterparty_id IS NOT NULL)
    OR (kind IN ('self_fact', 'domain_fact') AND counterparty_id IS NULL)
  ),

  -- Rule 15 corollary: distinct count cannot exceed total.
  CHECK (distinct_source_count <= source_count),

  -- Rule 14: source_event_ids must not be empty.
  CHECK (json_array_length(source_event_ids) >= 1)
);

CREATE INDEX ix_neurons_persona_kind_active
  ON neurons(persona_id, kind)
  WHERE superseded_at IS NULL;

CREATE INDEX ix_neurons_counterparty_active
  ON neurons(counterparty_id)
  WHERE counterparty_id IS NOT NULL AND superseded_at IS NULL;

CREATE INDEX ix_neurons_embedder
  ON neurons(embedder_rev)
  WHERE superseded_at IS NULL;

-- Vector index for Phase 1 retrieval.
-- Requires sqlite-vec extension; see docs/runbooks/sqlite_vec_install.md.
CREATE VIRTUAL TABLE neurons_vec USING vec0(
  neuron_id INTEGER PRIMARY KEY,
  embedding FLOAT[384]
);
