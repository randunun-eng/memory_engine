## Phase

- [ ] Phase: <e.g., Phase 2>
- Link to phase doc: `docs/phases/PHASE_N.md`
- Acceptance criterion this PR advances:

## Summary

<What does this PR do? One or two sentences. Not a diff summary.>

## Why

<The reasoning for the change. Reference the phase doc section or blueprint passage if relevant.>

## Governance check

- [ ] No new LLM call sites outside `src/memory_engine/policy/` (rule: single policy plane).
- [ ] No direct `UPDATE` or `DELETE` on `events` (rule 1).
- [ ] Raw SQL only; no ORM introduced (ADR 0003).
- [ ] New invariants added have tests in `tests/invariants/`.
- [ ] Any schema changes are in a new migration file (no edits to applied migrations).
- [ ] Secrets: no hardcoded credentials, no `.env.local` committed.

## Tests

- [ ] Added/updated unit tests.
- [ ] Added/updated integration tests.
- [ ] Added/updated invariant tests (if governance-relevant).
- [ ] All tests green locally: `uv run pytest`.
- [ ] Ruff and mypy pass: `uv run ruff check && uv run mypy src/`.

## Drift

- [ ] No drift from blueprint introduced.
- [ ] Drift introduced and documented in `docs/blueprint/DRIFT.md`.
- [ ] Drift resolves an existing DRIFT entry.

## Reviewer notes

<Anything that needs reviewer attention beyond the diff. Particularly: surprising performance changes, new dependencies, public API changes.>
