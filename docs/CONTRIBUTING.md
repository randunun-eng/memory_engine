# Contributing to memory_engine

> Before anything else, read `CLAUDE.md` at the repository root. It is the authoritative instruction file. This document is supplementary.

## Who contributes

Right now, a solo operator building against the blueprint. As the project matures, contributors will join; this document exists so the expectations are already written down when that happens.

## Before you open a PR

1. **Read CLAUDE.md end to end, once.** Future sessions can skim, but first-timers need the full context.
2. **Read the phase document for the phase you're touching.** `docs/phases/PHASE_N.md`. It contains the acceptance criterion.
3. **Run the full test suite locally:** `uv run pytest -v`. Green on main is a contract.
4. **Run lint and types:** `uv run ruff check` and `uv run mypy src/`. Both must pass.

## Branch and commit conventions

- Branch off `main`. Name: `phase-N/short-description` or `fix/short-description`.
- Conventional commits. Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `migration`.
- Every commit references a phase: `feat(phase0): add event append`.
- Commit body explains *why*, not *what*. The diff shows the what.
- Migrations are their own commits. Never bundle a migration with code that uses it — two commits, small migration first.

## PR conventions

- One phase acceptance criterion per PR when possible. Large PRs that touch multiple phases are harder to review and harder to revert.
- PR description answers three questions:
  - What acceptance criterion does this advance (reference to `docs/phases/PHASE_N.md`)?
  - What tests prove the advance?
  - Any blueprint drift introduced? (If yes, update `docs/blueprint/DRIFT.md` in the same PR.)
- Reviewer checks:
  - All tests pass
  - Governance rules from CLAUDE.md §4 still hold
  - Phase document acceptance criterion is demonstrably met
  - No new dependencies without discussion
  - No changes to files in "things Claude must never do without asking" (CLAUDE.md §12) without explicit approval note

## Review expectations

- Solo work: you may self-merge, but write the PR description anyway. It's your future self's documentation.
- Multi-contributor: at least one approving review from someone other than the author.
- Automated: CI must be green. Integrity check (`.github/workflows/integrity.yml`) must pass — this validates that governance invariants still hold.

## What to raise as an issue, not a PR

- Contradiction between blueprint documents
- Phase acceptance criteria that turn out to be unreachable
- Proposed governance rule change
- Proposed new dependency
- Proposed breaking migration

These deserve discussion before code. Use GitHub issues with the appropriate label.

## Drift management

If implementation diverges from the blueprint during a PR:

1. Document the divergence in `docs/blueprint/DRIFT.md` (append-only).
2. Choose a status: `deferred` (will reconcile later), `accepted` (update blueprint), `under_review`.
3. Reference the DRIFT entry in the PR description.

The blueprint is long-lived source of truth. Code drifts faster. DRIFT.md keeps the two honest with each other.

## What not to do

- Do not write code that violates CLAUDE.md §4 (Governance Rules). Ever.
- Do not make privacy invariants probabilistic. They are hard, not soft.
- Do not add LLM call sites outside `src/memory_engine/policy/dispatch.py`.
- Do not commit secrets. CI runs secret scans; pre-commit hooks run gitleaks.
- Do not merge with failing tests. Red main blocks everyone.

## Getting unstuck

When you don't know what to do:

1. Re-read the phase document.
2. Re-read the relevant blueprint section.
3. Check `docs/adr/` for whether the decision has already been made.
4. Ask (GitHub issue or team channel). An hour of confusion is not worth a day of wrong code.
