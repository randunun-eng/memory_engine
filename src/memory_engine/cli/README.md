# `memory_engine.cli`

Command-line entry points. Installed as the `memory-engine` console script by `pyproject.toml`.

## What belongs here

- `main.py` — root Click group; imports and registers subgroups.
- `db.py` — `memory-engine db migrate|status`.
- `serve.py` — `memory-engine serve` (starts HTTP + background tasks).
- `seed.py` — `memory-engine seed-neurons` for Phase 1 fixtures.
- `prompt.py` — `memory-engine prompt list|show|add|shadow|promote|rollback` (Phase 6).
- `heal.py` — `memory-engine heal run-once|status` (Phase 3).
- `halt.py` — `memory-engine halt status|release|force` (Phase 3).
- `identity.py` — `memory-engine identity init-owner|load|show|drift` (Phase 4).
- `mcp.py` — `memory-engine mcp register|revoke|list` (Phase 5).
- `wa.py` — `memory-engine wa verify-webhook|test-send` (Phase 5).

## What does NOT belong here

- Business logic. CLI commands are thin — parse args, call a domain function, format output.
- Long-running loops (→ `memory_engine.healing.runner`, `memory_engine.core.consolidator`).

## Conventions

- Every command uses `asyncio.run()` once at the top to bridge sync Click to async domain code.
- Output is human-readable first, JSON on `--json` flag when the command is scriptable.
- Destructive commands (`halt force`, `prompt rollback`, `db migrate` on Postgres) prompt for confirmation unless `--yes` is passed.
