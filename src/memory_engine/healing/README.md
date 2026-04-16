# `memory_engine.healing`

Declarative invariants, the healer loop, halt state management.

## What belongs here

- `registry.py` — the `@invariant(rule=N, severity=...)` decorator and the global registry.
- `invariants.py` — one function per governance rule (or per specific check). Each decorated.
- `runner.py` — the periodic scan loop. Executes every registered invariant every N seconds.
- `halt.py` — halt state as log events. `is_halted()`, `force_halt()`, `release_halt()`.
- `repair.py` — safe repair actions. Phase 3 ships only two (stale working-memory prune, orphan `neurons_vec` row cleanup). Everything else is detected and logged; repair is operator-driven.

## What does NOT belong here

- Rules 1–16 themselves. Those are defined in `CLAUDE.md` §4 and enforced by invariants here.
- Application of invariants during normal write paths. That's the write path's job (CHECK constraints, triggers, etc.). This module *checks* state; it doesn't gate it.
- Business logic fixes. If a bug produces bad state, fix the bug; this module detects the result.

## Meta-invariant

`tests/invariants/test_all_rules_have_invariants.py` imports `registered_invariants()` and asserts that every rule 1..16 has at least one registered check. Adding a rule to `CLAUDE.md` §4 without a check fails CI.

## Severity semantics

- **INFO** — observed; logged; no automatic action.
- **WARNING** — logged; included in dashboards; human reviews.
- **CRITICAL** — logged; transitions the engine to read-only. `/v1/ingest` returns 503; `/v1/recall` continues. Operator releases halt via CLI.

## Halt state is in the log

`is_halted()` queries the event log for the most recent `halted` vs `halt_released` events. This means:

- Halt survives restarts.
- Halt history is auditable.
- Halt release requires a reason (part of the `halt_released` event payload).
- Double-halt is a no-op.

## Conventions

- Every invariant is pure: it reads state, returns a list of violations. No writes from the check function.
- Critical checks are run twice (100ms apart) to filter out race conditions before halting. See `runner._confirm_critical()`.
- Invariant names become log keys and metric labels. Keep them stable. If you rename, update every reference.
