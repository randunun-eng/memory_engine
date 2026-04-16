# `memory_engine.db`

Database connection management and migration running. No business logic.

## What belongs here

- `connection.py` — async connection factory. Sets pragmas (WAL, foreign keys, synchronous NORMAL), loads sqlite-vec extension.
- `migrations.py` — migration runner. Applies `migrations/*.sql` in order, records checksums in `schema_migrations`.
- `exceptions.py` — DB-layer exceptions (`MigrationError`, `UpdateForbidden`, `DeleteForbidden`).
- `dialect.py` (Phase 3+) — SQLite-to-Postgres translation for the few cases they diverge (TIMESTAMPTZ, BIGSERIAL, JSONB, vector column).

## What does NOT belong here

- Queries against core tables — those live with their domain module (`core/events.py` owns events queries).
- Schema definitions — those live in `migrations/*.sql`.
- ORMs. See ADR 0003.

## Conventions

- SQLite is the default; Postgres is the scale-up. See ADR 0002.
- Migrations are forward-only and additive until v1.0. Never edit an applied migration; add a new one.
- Checksums catch edit-after-apply. If you see `MigrationError: checksum mismatch`, someone broke the rule.
