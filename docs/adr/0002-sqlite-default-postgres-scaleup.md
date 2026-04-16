# 0002 — SQLite by default, Postgres as scale-up

## Status

Accepted — 2026-04-16

## Context

memory_engine needs a durable store for: the event log (append-only, immutable), derived neurons, vector embeddings, graph edges, healing logs, prompt templates, retrieval traces, and various smaller tables.

Deployment targets in descending order of likelihood:
1. A single Oracle Cloud ARM VM (4 OCPU / 24 GB RAM) running the engine and DB on the same host.
2. A Raspberry Pi or small home server for hobbyist deployments.
3. A cloud-hosted Postgres instance (Supabase, Neon, Timescale Cloud) for users with existing infrastructure.
4. A larger multi-persona deployment with hundreds of thousands of events per persona.

The workload characteristics:
- Single writer (consolidator), multiple readers (retrieval).
- Read-heavy: retrievals outnumber ingests 10:1 or more in production.
- Vector similarity: needed per-query, small (hundreds of candidates), not massive.
- Graph queries: bounded-depth walks (≤ 2 hops in Phase 1).
- Time-series aspect: events ordered by recorded_at, rarely random-access.
- Transactional atomicity required (consolidator's extract-and-promote-and-contradict steps).

Databases considered:
- **SQLite** with sqlite-vec for vectors and standard schema for the rest.
- **Postgres** with pgvector and native JSONB.
- **DuckDB** — columnar, excellent for analytics, weaker for OLTP patterns we need.
- **LanceDB** — vector-first. Excellent for vectors, awkward for relational schema.
- **Chroma, Qdrant, Weaviate** — vector stores. Rejected because we need vectors alongside SQL-enforceable invariants (CHECK constraints, triggers, foreign keys).

The question is not "which is better in absolute terms" but "which provides the best default, given that users will also want Postgres as a scale-up path."

## Decision

**Default: SQLite + sqlite-vec.** All Phase 0 development targets SQLite. The Python code uses `aiosqlite` and raw parameterized SQL.

**Scale-up: Postgres + pgvector.** From Phase 3 onward, the migration runner includes a `-- postgres:` variant section for migrations where SQLite and Postgres syntax diverge. A `db.dialect` module translates common cases (datetime defaults, BIGSERIAL vs INTEGER PRIMARY KEY, JSON vs JSONB, vector column placement, triggers).

Operators choose by setting `MEMORY_ENGINE_DB_BACKEND=postgres` and providing `MEMORY_ENGINE_DB_URL=postgresql+asyncpg://...`. The engine adapts.

## Consequences

**Easier:**
- Zero install for Phase 0. `uv sync` installs aiosqlite and sqlite-vec; the DB is a file.
- Deployment to a single VM is trivial — no separate DB server, no pg_hba.conf.
- Backups are a file copy (with `.backup` API to get a consistent snapshot).
- Local development has no "did I forget to start Postgres" friction.
- Tests use `:memory:` SQLite, instant teardown.
- Schema migrations are SQL files; dialect translation is a thin layer.

**Harder:**
- Two dialects to support indefinitely. Schema files live in SQLite grammar; the translator converts for Postgres. Cost: occasional per-migration attention to syntactic differences.
- Concurrent writes on SQLite are serialized. Our architecture assumes single-writer-per-table anyway (rule 9), so this aligns, but we must hold the line on that rule.
- SQLite's type system is dynamic. Postgres is strict. Tests must run against both to catch type drift.
- sqlite-vec is newer than pgvector; fewer deployments in the wild. It's stable enough for our workload, but we carry more risk here than with pgvector.

**Future constraints:**
- Anything that depends on a Postgres-only feature (LISTEN/NOTIFY, trigram indexes) must be optional, with a SQLite fallback.
- If SQLite becomes the bottleneck for a specific persona, that operator migrates to Postgres — and the migration must be documented and tested.

## Alternatives considered

- **Postgres as default.** Rejected because it imposes infrastructure overhead on the default path. A Raspberry Pi deployment should not require a Postgres install. An operator who never scales past one persona never needs Postgres.
- **Vector-store-first (Chroma / Qdrant).** Rejected because our invariants need SQL. CHECK constraints on scope, foreign keys on persona_id, triggers for immutability — these cannot be enforced by a vector store. Vectors are one part of retrieval, not the primary data model.
- **MySQL / MariaDB.** Rejected for weaker JSON support and the absence of a vector extension with pgvector-equivalent maturity.
- **DuckDB.** Rejected because the workload is OLTP with occasional analytics, not analytics-first. DuckDB does OLTP, but less ergonomically than SQLite or Postgres.
- **SQLite + separate vector store** (SQLite for relational, Qdrant for vectors). Rejected because dual-store means dual-consistency concerns, dual-backup, dual-failure-modes. Keeping everything in one engine until forced to split is the simpler default.

## Revisit if

- sqlite-vec development stalls for more than 12 months.
- A deployment legitimately needs > 10 million neurons per persona (SQLite can handle it, but Postgres is more comfortable past that size).
- Concurrent-writer requirements emerge (would violate rule 9 — but if the rule itself is revisited, so is this ADR).
- A clearly superior embedded vector-capable engine ships (no such thing exists in 2026; this is a "watch for it" item).
