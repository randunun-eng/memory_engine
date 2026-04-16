# Migrations

Forward-only, numbered, additive. SQLite-targeted with a Postgres-compatible subset.

## Conventions

- File name: `NNN_short_description.sql` where NNN is a zero-padded three-digit sequence number.
- Applied in numerical order.
- Never edited after application — create a new migration to correct or extend.
- Additive only until v1.0: add columns, add tables, add indexes. No DROP, no RENAME.
- Every migration is recorded in `schema_migrations` by the runner.

## Planned migrations

| # | File | Phase | Status |
|---|---|---|---|
| 001 | `001_initial.sql` | Phase 0 | Planned |
| 002 | `002_consolidation.sql` | Phase 2 | Planned |
| 003 | `003_invariants.sql` | Phase 3 | Planned |
| 004 | `004_identity.sql` | Phase 4 | Planned |
| 005 | `005_adapters.sql` | Phase 5 | Planned |
| 006 | `006_observability.sql` | Phase 6 | Planned |

See CLAUDE.md §9 for the full phase plan and what each migration contains.

## Running migrations

```bash
uv run memory-engine db migrate        # apply all pending
uv run memory-engine db status         # show applied migrations
```

Never run raw SQL against a running deployment. Always go through a migration.
