# Changelog

All notable changes to memory_engine are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from v1.0 onward; pre-1.0 versions are 0.<phase>.<patch>.

## [Unreleased]

*(nothing yet — Phase 4 work will land here)*

---

## [0.4.0] - 2026-04-16

### Added
- Migration 003: healing_log table with severity/status CHECK constraints and partial indexes (`migrations/003_invariants.sql`).
- Invariant registry: 21 declarative checks across all 16 governance rules, using raw SQL exclusively (`healing/invariants.py`).
- Invariant checker: runs full or critical-only scans, records violations to healing_log, triggers halt on critical (`healing/checker.py`).
- Halt mechanism: in-memory HaltState singleton + durable healing_log record; `assert_not_halted()` gate for API handlers (`healing/halt.py`).
- Auto-repair library: `repair_missing_provenance` (quarantines neuron), `repair_distinct_count_mismatch` (corrects count) (`healing/repair.py`).
- Rule 1: trigger existence check queries sqlite_master for events_immutable_update and events_immutable_delete (schema drift defense).
- Rule 12: 3 checks — cross-counterparty source mismatch, counterparty_fact requires counterparty_id, self/domain facts must not have counterparty_id.
- Rule 14: 2 checks — empty source_event_ids, dangling citations to non-existent events.
- Rule 15: 2 checks — distinct_source_count > source_count, distinct count vs actual unique source IDs.
- Phase 3 test suite: 12 integration + 13 invariant tests, all passing.

### Verified
- All 16 governance rules have at least one registered invariant check (meta-test).
- Rules 12, 14, 15 have multiple checks covering different attack vectors.
- Critical violations engage halt; warnings log but don't halt.
- Halt release clears in-memory flag and marks healing_log entries resolved.
- Synthetic cross-counterparty injection detected in single scan.

Refs: phase-3-complete

---

## [0.3.0] - 2026-04-16

### Added
- Migration 002: working_memory, quarantine_neurons, episodes, prompt_templates tables (`migrations/002_consolidation.sql`).
- Policy dispatch: single entry point for all LLM calls with cache, template resolution, and response parsing (`policy/dispatch.py`).
- Prompt registry: loads versioned markdown templates with YAML frontmatter, hot-reload support (`policy/registry.py`).
- Context broker: parameter validation and filtering per prompt site, prevents injection via unexpected fields (`policy/broker.py`).
- Prompt cache: LRU cache keyed on (site, prompt_hash, input_hash, persona_id) — persona_id in key prevents cross-persona poisoning (`policy/cache.py`).
- LLM-driven entity extraction producing NeuronCandidate objects from events (`core/extraction.py`).
- Grounding gate: citation resolution + cosine similarity check + optional LLM judge for semantic/procedural tiers (`core/grounding.py`).
- Contradiction detection: same-entity-pair keyword overlap heuristic + LLM judge, with supersession on contradict (`core/contradiction.py`).
- Consolidator: promote → reinforce → decay → prune loop with exponential activation decay (`core/consolidator.py`).
- Quarantine: rejected neuron candidates written to quarantine_neurons table, not silently dropped.
- New exceptions: PromptNotFound, DispatchError, LLMResponseParseError, GroundingRejection.
- Phase 2 test suite: 10 integration + 5 invariant tests, all passing.

### Verified
- Rule 14: empty source_event_ids rejected by CHECK constraint.
- Rule 15: distinct_source_count <= source_count enforced; echo citations do not inflate distinct count (mem0 audit).
- Rule 16: NULL t_valid_start preserved through extraction and promotion pipeline.

Refs: phase-2-complete

---

## [0.2.0] - 2026-04-16

### Added
- Full retrieval plane: BM25 + vector + graph (empty Phase 1) + RRF fusion (`src/memory_engine/retrieval/`).
- Recall API: `recall(query, lens, as_of, top_k, token_budget)` with citations and per-neuron scores (`retrieval/api.py`).
- Lens enforcement via parameterized SQL WHERE clauses — structural cross-counterparty isolation (rule 12) (`retrieval/lens.py`).
- BM25 retrieval stream with lowercase tokenizer, no stemming, as_of temporal support (`retrieval/bm25.py`).
- Vector retrieval stream via sqlite-vec cosine distance with embedder_rev filtering (`retrieval/vector.py`).
- Reciprocal Rank Fusion with k=60 damping, source tracking per result (`retrieval/fuse.py`).
- Async retrieval_trace emission via fire-and-forget task (rule 7) (`retrieval/trace.py`).
- Token budget truncation (~4 chars/token estimate).
- HTTP surface: `POST /v1/recall` with Pydantic request/response models (`http/routes/recall.py`).
- Phase 1 baseline seed fixture: 27 neurons across persona alice_twin + 3 counterparties (`tests/fixtures/phase1_seed.py`).
- Phase 1 test suite: 12 integration + 8 invariant tests (including 5 T3-early canary tests), all passing.
- Eval baseline test scaffold (`tests/eval/test_recall_baseline.py`, requires `--eval` flag).

### Fixed
- BM25 score filter changed from `> 0.0` to `!= 0.0` to handle rank-bm25 BM25Okapi negative IDF in corpora with < 3 documents (see `docs/blueprint/DRIFT.md`).

Refs: phase-1-complete

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
