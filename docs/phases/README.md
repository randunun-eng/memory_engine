# Phase Plan

> Each phase has a self-contained specification document. A fresh Claude Code session opens the current phase's document and executes against it. No guessing.

## Current phase

**Phase 0** — Skeleton. See [`PHASE_0.md`](./PHASE_0.md).

## Phases at a glance

| # | Title | Doc | Duration | Depends on |
|---|---|---|---|---|
| 0 | Skeleton | [PHASE_0.md](./PHASE_0.md) | 2 weeks | — |
| 1 | Retrieval | [PHASE_1.md](./PHASE_1.md) | 3 weeks | Phase 0 |
| 2 | Consolidator + Grounding Gate | [PHASE_2.md](./PHASE_2.md) | 4 weeks | Phase 1 |
| 3 | Invariants + Healer | [PHASE_3.md](./PHASE_3.md) | 2 weeks | Phase 2 |
| 4 | Identity + Counterparties | [PHASE_4.md](./PHASE_4.md) | 3 weeks | Phase 3 |
| 5 | WhatsApp Adapter | [PHASE_5.md](./PHASE_5.md) | 3 weeks | Phase 4 |
| 6 | Blocking Gaps | [PHASE_6.md](./PHASE_6.md) | 4 weeks | Phase 5 |
| 7 | First Internal User | [PHASE_7.md](./PHASE_7.md) | 3 weeks | Phase 6 |

Total: 24 weeks half-time solo, to a defensibly operable first production deployment.

## How to use a phase document

Each phase document contains:

1. **Goal** — one paragraph.
2. **Prerequisites** — phases that must be complete, inputs expected.
3. **Schema changes** — full SQL, ready to run as a migration.
4. **Python modules to create** — with file paths, signatures, docstrings, pseudocode.
5. **Tests to write** — exact test names and expected behavior.
6. **Acceptance criterion** — the single condition that marks the phase complete.
7. **Out of scope for this phase** — explicit non-goals so scope creep is visible.
8. **Common pitfalls** — phase-specific traps observed during blueprint design.

A fresh Claude Code session should be able to read the phase document and execute without needing to re-derive intent from the blueprint. Blueprint is the *why*; phase document is the *what* and *how*.

## Rule: stay within the current phase

A phase document's acceptance criterion is the boundary. Do not add functionality that belongs to a later phase just because it feels natural; that creates coupling across phase documents and breaks the ability to merge phase by phase.

If during a phase you discover that a later phase's work is unavoidable for correctness, raise it in `docs/blueprint/DRIFT.md` and discuss before expanding scope.

## Rule: finish a phase before starting the next

Acceptance criterion must be green (tests passing, criterion demonstrably met) before moving on. Incomplete phases produce debt that compounds.

The exception: blocking gaps (`PHASE_6`) may be pursued in parallel with Phase 5 work because they share plumbing (observability instrumentation, prompt registry schema). This is the only sanctioned concurrency.

## Rule: document drift as it happens

When implementation diverges from the phase document, the phase document is probably wrong (blueprint-informed assumptions that turned out to be unreachable). Update `DRIFT.md`, update the phase document, and proceed. The documents track reality; reality does not bend to the documents.
