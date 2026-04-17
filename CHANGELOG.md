# Changelog

All notable changes to memory_engine are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from v1.0 onward; pre-1.0 versions are 0.<phase>.<patch>.

## [Unreleased]

*(nothing yet — Phase 7 work will land here)*

---

## [0.7.0] - 2026-04-16

### Added
- Migration 006: retrieval_traces (async reinforcement traces), prompt_shadow_logs (per-execution A/B logs), prompt_comparison_daily (aggregated daily metrics), backup_status (last successful backup per persona) (`migrations/006_observability.sql`).
- Prometheus metric registry: thread-safe counter/gauge/histogram with Prometheus text exposition format, no external client library dependency (`observability/metrics.py`).
- Structured JSON logger: JSONFormatter + StructuredLogger wrapper enforcing required fields ts/level/module/event (`observability/logging.py`).
- Prompt shadow harness: `dispatch_with_shadow` runs active + optional shadow at configurable traffic percentage, logs comparisons, guarded by rng injection for deterministic testing (`policy/shadow.py`).
- Daily comparison batch: `compute_daily_comparison` aggregates shadow logs into per-site metrics (sample count, latency delta, cost delta, output agreement rate).
- Promotion and rollback: `promote_shadow` clears old active and promotes shadow (clearing shadow flag); `rollback_to_template` restores arbitrary previous template as active.
- `bin/restore.sh`: decrypt → verify manifest + SQLite integrity_check → backup existing DB to `.pre-restore` → swap → restart engine. Supports S3/GCS/local artifact sources. Interactive confirmation unless `--force`.
- `bin/drill.sh`: pulls latest backup → decrypts → verifies → measures elapsed vs RTO → writes `drills/YYYY-MM-DD.md` report with verdict, timing, integrity, row counts.
- 3 Grafana dashboard JSONs: operations.json (on-call single-screen), memory_health.json (weekly review), per_persona.json (parameterized diagnosis) in `dashboards/`.
- Prometheus alert rules file (`dashboards/alerts.yaml`): 4 critical (HardInvariantViolation, MCPAuthFailureSpike, EventLogStalled, BackupStale) + 7 warning (DistinctSourceRatioDrop, QuarantineDepthGrowing, GroundingGateRejectRateHigh, RecallLatencyP99High, LLMSpendRateHigh, ConsolidatorLagging, IdentityFlagSpike), each with runbook annotation.
- 11 new runbooks in `docs/runbooks/` (hard_invariant, mcp_compromise, ingest_stalled, echo_inflation, quarantine_review, extractor_quality, retrieval_latency, cost_overrun, consolidator_lag, identity_drift, backup_stale), one page each with diagnostic steps and remediation.
- Phase 6 test suite: 24 integration + 7 invariant tests, all passing.
- **First DR drill completed**: `drills/2026-04-16.md` — verdict PASS, elapsed 0s, well under 2-hour RTO target.

### Verified
- Prometheus text exposition format renders counter, gauge, and histogram series with proper label escaping and +Inf histogram bucket.
- Counter rejects negative increments (counters are monotonic).
- JSONFormatter produces valid JSON with required fields (ts, level, module, event).
- Shadow harness runs only active when no shadow configured; runs both when rng draw < traffic_pct; skips shadow when draw >= threshold.
- Daily comparison aggregates shadow logs correctly (sample count, mean latency/cost, output agreement).
- Promotion clears old active, activates shadow, clears shadow flag and traffic_pct.
- Rollback restores arbitrary previous template as active in <60s (requirement per spec).
- Meta-test: every alert has a corresponding runbook file.
- Meta-test: all 3 dashboards parse as valid JSON; operations dashboard has all required panels.
- Backup round-trip: actual `bin/backup.sh` → age-encrypt → `bin/restore.sh` → decrypt → integrity_check → row count match (tested via subprocess in test_phase6.py).

Refs: phase-6-complete

---

## [0.6.0] - 2026-04-16

### Added
- Migration 005: mcp_sources table (per-persona MCP binding with Ed25519 public key + hashed bearer token), tombstones table (soft deletion / reingestion prevention), sender_hint column on events for group messages (`migrations/005_adapters.sql`).
- Phone canonicalization: strips formatting, ensures E.164 with "whatsapp:+" prefix, validates 7-15 digit length (`adapters/whatsapp/canonicalize.py`).
- Group JID canonicalization: normalizes to "whatsapp-group:<jid>" format, validates @g.us suffix.
- MCP source management: register (generate bearer token, store public key), resolve_token (authenticate API requests), revoke (soft-delete) (`adapters/whatsapp/mcp.py`).
- WhatsApp ingest pipeline: 8-step sequential processing — token resolution, signature verification, phone/JID canonicalization, counterparty lookup/create, tombstone check, payload construction, idempotency key, event append (`adapters/whatsapp/ingest.py`).
- Group message handling: groups-as-counterparty with sender_hint stored on events for audit; sender_hint never creates sub-counterparties, never used in retrieval (`adapters/whatsapp/groups.py`).
- Outbound preparation: integrates Phase 4 approval pipeline, creates persona_output event on approval, no event on block (`adapters/whatsapp/outbound.py`).
- Forwarded message handling: attributes to forwarder, stores forwarded_from in payload, no counterparty for original author.
- Image reference pass-through: stored in event payload, not processed (Phase 5 scope: text only).
- Tombstone enforcement: checks counterparty, content_hash, and idempotency scopes.
- append_event() extended with optional mcp_source_id and sender_hint parameters (set at INSERT time; immutability trigger prevents post-INSERT UPDATE).
- T11 adversarial corpus: 21 prompts across 6 attack categories (direct injection, role-play, encoding tricks, context-window manipulation, indirect injection, SQL injection).
- Phase 5 test suite: 18 integration + 9 invariant tests, all passing.

### Fixed
- Phase 4 blocked-event PII leak: drift flags and blocked ApprovalResult.text now contain PII-redacted text (rule 13 enforcement in audit trail).
- LLM judge security documentation: added SECURITY NOTE in outbound/approval.py about nonneg_judge receiving unredacted text.

### Verified
- T3 release gate: 100% pass. 4 tests including 100-message/5-counterparty acceptance criterion. Zero cross-counterparty content leaks.
- T11 release gate: 100% pass. 5 tests with 21-prompt adversarial corpus. Zero identity modifications, zero cross-counterparty leaks, zero outbound bypass, zero SQL injection impact.
- Phone variants ("+94 77 123 4567", "+94-77-123-4567", "94771234567") all map to one counterparty.
- Group A events fully isolated from Group B events.
- Individual Alice events fully isolated from group events containing Alice as sender_hint.
- Tombstones block reingestion at counterparty and content-hash scopes.
- Idempotency key prevents duplicate WhatsApp message ingestion.

Refs: phase-5-complete

---

## [0.5.0] - 2026-04-16

### Added
- Migration 004: identity_drift_flags + tone_profiles tables with CHECK constraints on flag_type and reviewer_action (`migrations/004_identity.sql`).
- Identity document loader: parse/load/save YAML identity documents with self_facts, non_negotiables, forbidden_topics, deletion_policy (`identity/persona.py`).
- Identity drift detection: flag_identity_drift writes to drift table for human review, check_forbidden_topics (substring match), check_self_fact_contradiction (negation heuristic) (`identity/drift.py`).
- Outbound approval pipeline: 5-step sequential evaluation — non-negotiables, forbidden topics, self-contradiction, cross-counterparty redaction, PII redaction (`outbound/approval.py`).
- Keyword-based non-negotiable evaluator: extracts patterns from rules ("never disclose X", "never discuss X", "never agree to X without Y") as fallback when no LLM dispatch available.
- PII redactor: regex-based stripping of emails, phones, SSN-like patterns, API keys/tokens, with allowed-set bypass for active counterparty (`outbound/redactor.py`).
- Cross-counterparty name redactor: queries DB for persona's counterparties, strips non-active names from outbound text.
- Identity example template: `config/identity.example.yaml` with all supported fields.
- New exception: OutboundBlocked for outbound pipeline blocking.
- Prompt sites: nonneg_judge and self_contradiction_judge registered in broker.
- Phase 4 test suite: 22 integration + 9 invariant tests, all passing.

### Fixed
- Blocked-event PII leak: drift flags and blocked ApprovalResult.text now contain PII-redacted text, not the original unredacted draft. The event log is immutable; a PII leak in the audit trail stays forever. Rule 13 (privacy > everything) enforced in the audit path.
- Added SECURITY NOTE in outbound/approval.py docstring: nonneg_judge LLM receives unredacted text for accurate evaluation; operators using remote LLM APIs are sending counterparty PII to a third party and should configure routing accordingly.

### Verified
- Rule 11: drift flags never modify personas.identity_doc — identity remains authoritative, human-only.
- Rule 13: privacy redaction applies after persona/factual checks pass (pillar hierarchy enforced).
- Rule 13: blocked drift flags contain redacted PII, not raw PII (2 new invariant tests).
- Non-negotiable enforcement blocks email/phone disclosure, unauthorized meeting agreement, unconfirmed pricing.
- Forbidden topic detection blocks politics and other_clients_by_name.
- Self-contradiction detection catches negations of self_facts.
- PII redactor preserves allowed counterparty emails/phones.
- Cross-counterparty redactor preserves active counterparty name while stripping others.
- No identity doc scenario: outbound approved with warning (dev/test fallback).

Refs: phase-4-complete

---

## [0.4.0] - 2026-04-16

### Added
- Migration 003: healing_log + halt_state tables with severity/status CHECK constraints and partial indexes (`migrations/003_invariants.sql`).
- Invariant registry: 21 declarative checks across all 16 governance rules, using raw SQL exclusively (`healing/invariants.py`).
- Invariant checker: runs full or critical-only scans, records violations to healing_log, triggers halt on critical (`healing/checker.py`).
- Halt mechanism: durable halt_state table (singleton row, survives restart) + in-memory HaltState flag; `load_halt_state()` at startup, `assert_not_halted()` gate for API handlers (`healing/halt.py`).
- Healer loop: asyncio background task scanning every 60s, survives individual check exceptions (`healing/loop.py`).
- Auto-repair library: `repair_missing_provenance` (quarantines neuron), `repair_distinct_count_mismatch` (corrects count) (`healing/repair.py`).
- Rule 1: trigger existence check queries sqlite_master for events_immutable_update and events_immutable_delete (schema drift defense).
- Rule 12: 3 checks — cross-counterparty source mismatch, counterparty_fact requires counterparty_id, self/domain facts must not have counterparty_id.
- Rule 14: 2 checks — empty source_event_ids, dangling citations to non-existent events.
- Rule 15: 2 checks — distinct_source_count > source_count, distinct count vs actual unique source IDs.
- Phase 3 test suite: 17 integration + 13 invariant tests, all passing.

### Verified
- All 16 governance rules have at least one registered invariant check (meta-test).
- Rules 12, 14, 15 have multiple checks covering different attack vectors.
- Critical violations engage halt; warnings log but don't halt.
- Halt survives simulated restart (load from halt_state table).
- Halt release is durable (active=0 persists across restart).
- Healer loop runs at least one scan and halts on critical violation.
- Healer loop survives exceptions in individual checks.
- Synthetic cross-counterparty injection detected in single scan.
- Scan baseline: 3ms at 50 events + 10 neurons; ~0.6s extrapolated to 10k (30s budget).

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
