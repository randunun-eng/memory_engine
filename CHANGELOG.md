# Changelog

All notable changes to memory_engine are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from v1.0 onward; pre-1.0 versions are 0.<phase>.<patch>.

## [Unreleased]

*(nothing yet — Phase 1 work will land here)*

---

## [0.1.0] - 2026-04-16

### Added
- Event append with Ed25519 signature verification (`src/memory_engine/core/events.py`).
- Idempotency enforcement via UNIQUE constraint on `idempotency_key`.
- SQLite migration runner with checksum verification (`src/memory_engine/db/migrations.py`).
- Async SQLite connection with WAL, foreign keys, and sqlite-vec extension loading (`src/memory_engine/db/connection.py`).
- Immutability triggers on events table (rule 1), producing `OperationalError` on UPDATE/DELETE.
- Neurons table with CHECK constraints for kind/counterparty invariant and source citation requirement (rule 14).
- Pydantic settings model with TOML + env var loading (`src/memory_engine/config.py`).
- Ed25519 signing helpers for MCP envelope verification (`src/memory_engine/policy/signing.py`).
- CLI entry point: `memory-engine db migrate`, `memory-engine db status` (`src/memory_engine/cli/main.py`).
- Exception hierarchy rooted at `MemoryEngineError` (`src/memory_engine/exceptions.py`).
- Phase 0 test suite: 11 integration + 4 invariant tests, all passing.
- Phase 0 demo script: 10-event round-trip with hash verification (`examples/phase0_round_trip.py`).
- Initial repo scaffolding (CLAUDE.md, blueprint documents, phase specifications, ADRs, runbooks, CI, pre-commit, per-module READMEs).

### Changed
- Migration 001 immutability triggers use non-existent-table reference instead of `RAISE(ABORT, ...)` to produce `OperationalError` (see `docs/blueprint/DRIFT.md`).
- Removed speculative `WHEN OLD.type != 'halted'` guard from update trigger — halt events are inserted, never updated.

Refs: phase-0-complete

---

## Versioning scheme

- **Pre-1.0:** each Phase N close produces a `v0.N.0` tag.
  - `v0.0.0` — scaffolding only, not runnable.
  - `v0.1.0` — Phase 0 complete: event log round-trip.
  - `v0.2.0` — Phase 1 complete: retrieval.
  - `v0.3.0` — Phase 2 complete: consolidator + grounding gate.
  - `v0.4.0` — Phase 3 complete: invariants + healer.
  - `v0.5.0` — Phase 4 complete: identity + outbound approval.
  - `v0.6.0` — Phase 5 complete: WhatsApp adapter (T3 + T11 passing).
  - `v0.7.0` — Phase 6 complete: observability, backup/DR, prompt versioning.
  - `v0.8.0` — Phase 7 complete: three weeks operational with first internal user.

- **v1.0.0:** schema freeze and first stable release. Only reached after Phase 7 closes and a documented review of the blueprint-vs-implementation drift concludes no further scaffolding changes are needed.

- **Post-1.0:** standard semver. Breaking changes bump MAJOR.

## Commit-to-entry mapping

Each entry references the phase-close commit / tag. Example:

```
## [0.1.0] - 2026-05-14
### Added
- Event append with Ed25519 signature verification.
- Idempotency enforcement via UNIQUE constraint.
- SQLite migration runner with checksum verification.
- Immutability triggers on events (rule 1).
- pytest suite: 5 integration + 3 invariant tests.
- Phase 0 acceptance demonstrated: 10-event round-trip.

Refs: phase-0-complete
```

Populate when each phase closes. Do not edit the Unreleased section ahead of time.
