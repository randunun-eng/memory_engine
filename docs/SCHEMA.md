# Schema Reference

> Consolidated view of the full schema across all migrations. Source of truth for the database structure.
> When implementing a phase, the migration file is canonical; this document is the reading guide.

## Overview

SQLite is the default engine; Postgres is the scale-up path. Schema is designed for SQLite's type system (TEXT for ISO-8601 datetimes, JSON for JSONB-equivalent) with Postgres-compatible constructs preferred.

All tables have `id INTEGER PRIMARY KEY` (SQLite AUTOINCREMENT behavior) and a timestamp column — `recorded_at` for durable history, `created_at` for creation markers, `entered_at` for ring-buffer membership.

Migrations are numbered sequentially and applied in order. Each is additive until v1.0.

## Migration 001 — Initial (Phase 0)

The core tables that everything else builds on.

### personas

The top-level identity anchor. One per digital twin.

```sql
CREATE TABLE personas (
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  identity_doc    TEXT,                        -- YAML source, parsed at load
  version         INTEGER NOT NULL DEFAULT 1
);
```

- `slug` is a human-readable identifier: `randunu_sales_twin`. Used in logs and CLI.
- `identity_doc` is the full signed YAML. Parsed lazily by `memory_engine.identity.persona.load()`. Never modified by the LLM (rule 11).
- `version` supports optimistic concurrency if identity documents are edited by operator.

### counterparties

External entities the persona talks to — humans, groups, systems.

```sql
CREATE TABLE counterparties (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  external_ref    TEXT NOT NULL,               -- 'whatsapp:+94771234567' or 'whatsapp-group:120363...@g.us'
  display_name    TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (persona_id, external_ref)
);
```

- `external_ref` is canonicalized at ingress. Phone numbers: E.164 with channel prefix.
- UNIQUE constraint on `(persona_id, external_ref)` — same external ref can belong to different personas, but never duplicated within a persona.
- Groups are counterparty rows. The individual sender within a group is tracked via `events.sender_hint` (added in migration 005) but is never queried.

### events

The immutable event log. Rule 1: never updated, never deleted.

```sql
CREATE TABLE events (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  counterparty_id INTEGER REFERENCES counterparties(id),
  type            TEXT NOT NULL,               -- 'message_in', 'message_out', 'retrieval_trace', 'prompt_promoted', ...
  scope           TEXT NOT NULL CHECK (scope IN ('private', 'shared', 'public')),
  content_hash    TEXT NOT NULL,               -- SHA-256 of canonical content
  idempotency_key TEXT,                        -- unique per source
  payload         TEXT NOT NULL,               -- JSON
  signature       TEXT NOT NULL,               -- Ed25519 base64
  mcp_source_id   INTEGER,                     -- FK added in migration 005
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (idempotency_key)
);

CREATE INDEX ix_events_persona_recorded ON events(persona_id, recorded_at);
CREATE INDEX ix_events_counterparty ON events(counterparty_id) WHERE counterparty_id IS NOT NULL;
CREATE INDEX ix_events_type_persona ON events(persona_id, type);
```

- `content_hash` computed from canonical serialization of payload. Rule 14 references.
- `idempotency_key` prevents double-ingest; SOURCE-specific format.
- `signature` verified at ingress; never trusted without verification.
- No foreign key constraint to `mcp_sources` until migration 005 creates that table; the column is nullable until then.
- Immutability enforced by trigger (created in same migration):

```sql
CREATE TRIGGER events_immutable_update
BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are immutable (rule 1)');
END;

CREATE TRIGGER events_immutable_delete
BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are immutable (rule 1)');
END;
```

### neurons

Derived facts. Phase 0 creates the table; Phase 2 starts populating it.

```sql
CREATE TABLE neurons (
  id                      INTEGER PRIMARY KEY,
  persona_id              INTEGER NOT NULL REFERENCES personas(id),
  counterparty_id         INTEGER REFERENCES counterparties(id),
  kind                    TEXT NOT NULL CHECK (kind IN ('self_fact', 'counterparty_fact', 'domain_fact')),
  content                 TEXT NOT NULL,
  content_hash            TEXT NOT NULL,
  source_event_ids        TEXT NOT NULL,       -- JSON array of event ids
  source_count            INTEGER NOT NULL DEFAULT 1,
  distinct_source_count   INTEGER NOT NULL DEFAULT 1,
  tier                    TEXT NOT NULL CHECK (tier IN ('working', 'episodic', 'semantic', 'procedural')),
  t_valid_start           TEXT,                -- validity-time, world-truth, NULL if unknown
  t_valid_end             TEXT,
  recorded_at             TEXT NOT NULL DEFAULT (datetime('now')),
  superseded_at           TEXT,
  superseded_by           INTEGER REFERENCES neurons(id),
  embedder_rev            TEXT NOT NULL,
  version                 INTEGER NOT NULL DEFAULT 1,

  CHECK (
    (kind = 'counterparty_fact' AND counterparty_id IS NOT NULL)
    OR (kind IN ('self_fact', 'domain_fact') AND counterparty_id IS NULL)
  )
);

CREATE INDEX ix_neurons_persona_kind_active ON neurons(persona_id, kind) WHERE superseded_at IS NULL;
CREATE INDEX ix_neurons_counterparty_active ON neurons(counterparty_id) WHERE counterparty_id IS NOT NULL AND superseded_at IS NULL;
CREATE INDEX ix_neurons_embedder ON neurons(embedder_rev) WHERE superseded_at IS NULL;
```

- Rule 14: `source_event_ids` must be non-empty for every neuron.
- Rule 15: ranking uses `distinct_source_count`, not `source_count`.
- Rule 16: `t_valid_start` and `t_valid_end` may be NULL (unknown); never default to `now()`.
- Bi-temporal: `recorded_at` / `superseded_at` is recording-time; `t_valid_start` / `t_valid_end` is validity-time.
- `embedder_rev` is required so embedder rotation can migrate deliberately (see `docs/adr/0006-sentence-transformers-default-embedder.md`).

### neurons_vec (sqlite-vec virtual table)

Vector index for the vector retrieval stream.

```sql
CREATE VIRTUAL TABLE neurons_vec USING vec0(
  neuron_id INTEGER PRIMARY KEY,
  embedding FLOAT[384]
);
```

- 384-dimensional default for `sentence-transformers/all-MiniLM-L6-v2`.
- Populated alongside neurons; kept in sync by consolidator.

### schema_migrations

Tracks which migrations have been applied.

```sql
CREATE TABLE schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (datetime('now')),
  checksum   TEXT NOT NULL                     -- SHA-256 of migration SQL at apply time
);
```

- Checksum lets the runner detect if a migration file was edited after application (forbidden; would require rollback + reapply).

## Migration 002 — Consolidation (Phase 2)

Working memory, quarantine, episodes, prompt templates.

### working_memory

```sql
CREATE TABLE working_memory (
  id           INTEGER PRIMARY KEY,
  persona_id   INTEGER NOT NULL REFERENCES personas(id),
  event_id     INTEGER NOT NULL REFERENCES events(id),
  entered_at   TEXT NOT NULL DEFAULT (datetime('now')),
  activation   REAL NOT NULL DEFAULT 1.0
);

CREATE INDEX ix_working_persona_activation ON working_memory(persona_id, activation DESC);
```

- Ring buffer, bounded size per persona (configurable).
- Activation decays over time; consolidator reinforces on retrieval and on mention frequency.

### quarantine_neurons

```sql
CREATE TABLE quarantine_neurons (
  id                INTEGER PRIMARY KEY,
  persona_id        INTEGER NOT NULL REFERENCES personas(id),
  candidate_json    TEXT NOT NULL,
  reason            TEXT NOT NULL,             -- 'citation_unresolved', 'low_similarity', 'llm_judge_ungrounded'
  source_event_ids  TEXT NOT NULL,             -- JSON array
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at       TEXT,
  review_verdict    TEXT                       -- 'accept_into_cortex', 'discard', 'quarantine_indefinitely'
);

CREATE INDEX ix_quarantine_unreviewed ON quarantine_neurons(persona_id) WHERE reviewed_at IS NULL;
```

### episodes

```sql
CREATE TABLE episodes (
  id            INTEGER PRIMARY KEY,
  persona_id    INTEGER NOT NULL REFERENCES personas(id),
  start_event   INTEGER NOT NULL REFERENCES events(id),
  end_event     INTEGER NOT NULL REFERENCES events(id),
  summary       TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_episodes_persona ON episodes(persona_id);
```

### prompt_templates

The prompt registry for the policy plane. Foundation for Gap 3 closure in Phase 6.

```sql
CREATE TABLE prompt_templates (
  id                 INTEGER PRIMARY KEY,
  site               TEXT NOT NULL,                  -- 'extract_entities', 'classify_scope', ...
  version            TEXT NOT NULL,
  template_text      TEXT NOT NULL,
  parameters         TEXT NOT NULL,                  -- JSON schema
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  created_by         TEXT NOT NULL,
  active             INTEGER NOT NULL DEFAULT 0,
  shadow             INTEGER NOT NULL DEFAULT 0,
  shadow_traffic_pct REAL NOT NULL DEFAULT 0,
  notes              TEXT,
  UNIQUE (site, version)
);

CREATE UNIQUE INDEX ix_prompt_templates_active_per_site
  ON prompt_templates(site) WHERE active = 1;
```

## Migration 003 — Invariants (Phase 3)

Healing log. Synapses.

### healing_log

```sql
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
  ON healing_log(persona_id, severity) WHERE resolved_at IS NULL;
```

### synapses

Edges between neurons. Built by co-occurrence and explicit relation extraction.

```sql
CREATE TABLE synapses (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  source_neuron   INTEGER NOT NULL REFERENCES neurons(id),
  target_neuron   INTEGER NOT NULL REFERENCES neurons(id),
  relation        TEXT NOT NULL,                  -- 'related_to', 'contradicts', 'refines', ...
  weight          REAL NOT NULL DEFAULT 1.0,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (source_neuron, target_neuron, relation)
);

CREATE INDEX ix_synapses_source ON synapses(source_neuron);
CREATE INDEX ix_synapses_target ON synapses(target_neuron);
```

## Migration 004 — Identity (Phase 4)

Identity drift flags, tone profiles.

### identity_drift_flags

```sql
CREATE TABLE identity_drift_flags (
  id             INTEGER PRIMARY KEY,
  persona_id     INTEGER NOT NULL REFERENCES personas(id),
  flag_type      TEXT NOT NULL,               -- 'value_contradiction', 'role_drift', 'tone_drift'
  candidate_text TEXT NOT NULL,
  flagged_at     TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at    TEXT,
  reviewer_action TEXT                        -- 'accept', 'reject', 'quarantine'
);
```

### tone_profiles

```sql
CREATE TABLE tone_profiles (
  counterparty_id INTEGER PRIMARY KEY REFERENCES counterparties(id),
  profile_json    TEXT NOT NULL,
  analyzed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Migration 005 — Adapters (Phase 5)

MCP sources, tombstones, sender_hint on events.

### mcp_sources

```sql
CREATE TABLE mcp_sources (
  id                    INTEGER PRIMARY KEY,
  persona_id            INTEGER NOT NULL REFERENCES personas(id),
  kind                  TEXT NOT NULL,            -- 'whatsapp'
  name                  TEXT NOT NULL,
  public_key_ed25519    TEXT NOT NULL,            -- base64
  token_hash            TEXT NOT NULL,
  registered_at         TEXT NOT NULL DEFAULT (datetime('now')),
  revoked_at            TEXT,
  UNIQUE (persona_id, name)
);

CREATE INDEX ix_mcp_sources_active
  ON mcp_sources(persona_id, kind) WHERE revoked_at IS NULL;
```

### tombstones

```sql
CREATE TABLE tombstones (
  id            INTEGER PRIMARY KEY,
  persona_id    INTEGER NOT NULL REFERENCES personas(id),
  scope         TEXT NOT NULL,                   -- 'counterparty:X', 'event:Y', 'pattern:...'
  reason        TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_tombstones_persona_scope ON tombstones(persona_id, scope);
```

### events.sender_hint

```sql
ALTER TABLE events ADD COLUMN sender_hint TEXT;
```

- Populated for group messages (whose individual within the group sent the message).
- Never used in queries; stored for audit and potential future aggregate analysis.

## Migration 006 — Observability (Phase 6)

Retrieval traces, metrics-durable data.

### retrievals

```sql
CREATE TABLE retrievals (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  query_hash      TEXT NOT NULL,
  lens            TEXT NOT NULL,
  top_neurons     TEXT NOT NULL,                -- JSON array
  latency_ms      INTEGER NOT NULL,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_retrievals_persona_recorded ON retrievals(persona_id, recorded_at);
```

- Persistent store of retrieval traces, separate from the event log's `retrieval_trace` events.
- Used by consolidator for LTP reinforcement (rule 7: async read-write loop).

### prompt_comparison_daily

```sql
CREATE TABLE prompt_comparison_daily (
  id              INTEGER PRIMARY KEY,
  date            TEXT NOT NULL,                 -- YYYY-MM-DD
  site            TEXT NOT NULL,
  active_version  TEXT NOT NULL,
  shadow_version  TEXT NOT NULL,
  sample_size     INTEGER NOT NULL,
  metrics_json    TEXT NOT NULL,                 -- detailed metric deltas
  UNIQUE (date, site, shadow_version)
);
```

## Views and derived structures

### Active neurons

Queried frequently; Phase 2 adds a view:

```sql
CREATE VIEW active_neurons AS
SELECT * FROM neurons WHERE superseded_at IS NULL;
```

### Per-persona stats

Phase 6 adds a view for the dashboard:

```sql
CREATE VIEW persona_stats AS
SELECT
  p.id AS persona_id,
  p.slug,
  (SELECT count(*) FROM events WHERE persona_id = p.id) AS total_events,
  (SELECT count(*) FROM active_neurons WHERE persona_id = p.id) AS active_neurons,
  (SELECT count(*) FROM counterparties WHERE persona_id = p.id) AS counterparties
FROM personas p;
```

## Invariants encoded at the schema level

Not all invariants can be schema-level, but many can be. These are enforced by the DB, not just by Python code:

| Rule | Enforcement |
|---|---|
| 1 (events immutable) | Triggers on events: UPDATE and DELETE raise ABORT |
| 3 (scope CHECK) | `events.scope CHECK (scope IN ('private', 'shared', 'public'))` |
| 11 (counterparty_fact needs counterparty_id) | `neurons CHECK` constraint on kind / counterparty_id |
| 12 (cross-counterparty partition) | Partial indexes + SQL WHERE in retrieval (app layer) |
| 14 (every neuron cites ≥ 1 event) | `neurons.source_event_ids NOT NULL` + runtime non-empty JSON check |

## Query patterns

The common patterns that retrieval, consolidation, and healing rely on:

- **Events for a persona in time window:** `SELECT … FROM events WHERE persona_id = ? AND recorded_at BETWEEN ? AND ? ORDER BY recorded_at`
- **Active neurons for a persona filtered by lens:**
  ```sql
  -- counterparty lens
  SELECT … FROM neurons
  WHERE persona_id = ? AND superseded_at IS NULL
    AND (counterparty_id = ? OR kind = 'domain_fact')
  ```
- **Neurons citing a specific event:** `SELECT … FROM neurons WHERE json_array_length(source_event_ids) > 0 AND ? IN (SELECT json_each.value FROM json_each(source_event_ids))`
- **Supersession chain for a neuron:** recursive CTE from `neurons.superseded_by`.

## Postgres adaptations

When running on Postgres, the following changes apply:

- `TEXT NOT NULL DEFAULT (datetime('now'))` → `TIMESTAMPTZ NOT NULL DEFAULT now()`
- `INTEGER PRIMARY KEY` → `BIGSERIAL PRIMARY KEY`
- `TEXT` for JSON → `JSONB`
- `CREATE VIRTUAL TABLE neurons_vec USING vec0(...)` → `ALTER TABLE neurons ADD COLUMN embedding vector(384)`
- Triggers: Postgres syntax differs; the immutability trigger becomes a rule or a trigger function.

A compile step at migration runtime translates SQLite DDL to Postgres DDL where semantics match; for the few cases that diverge significantly (vector column placement), migration files have a `-- postgres:` section.

See `src/memory_engine/db/dialect.py` for the translation layer.

## Version notes

- **v0.x (pre-1.0):** additive migrations only. No DROP, no RENAME.
- **v1.0:** freeze schema. Any breaking change post-1.0 requires an ADR and a documented migration strategy.

When Phase 7 closes and we declare v1.0, this document is republished with the final additive-only schema baseline.
