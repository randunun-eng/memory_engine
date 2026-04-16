# Blueprint Drift Log

Known deviations between the blueprint documents in `docs/blueprint/` and the implementation in `src/`. Each entry is raised here first, then either resolved (reconcile code to blueprint), accepted (update blueprint), or deferred (explicit decision to diverge, with reason).

Entries are append-only. Once resolved, the entry stays with its resolution noted.

| Date | Area | Divergence | Resolution | Status |
|---|---|---|---|---|
| 2026-04-16 | migration/phase0 | Removed `WHEN OLD.type != 'halted'` guard on `events_immutable_update` trigger; changed trigger mechanism from `RAISE(ABORT, ...)` to non-existent-table reference to produce `OperationalError` instead of `IntegrityError` | Accepted — guard was speculative (halt events are inserted, never updated) and weakened rule 1 by creating an exception with no consumer; error type change aligns with what invariant tests check, and `OperationalError` is semantically more correct than `IntegrityError` for an immutability enforcement | Resolved |

## Conventions

- Raise a drift entry the same commit the divergence is introduced.
- Format: one-liner in the table above, full explanation in a subsection below.
- When resolving, update the Status column but do not edit the original entry text.

## Open entries

*(none)*

## Resolved entries

### 2026-04-16: Immutability trigger mechanism (migration 001)

The Phase 0 doc specified `RAISE(ABORT, 'events are immutable (rule 1)')` in the immutability triggers. Two issues:

1. **`RAISE(ABORT, ...)` in SQLite triggers produces `IntegrityError` in Python's sqlite3 module** (mapped from `SQLITE_CONSTRAINT_TRIGGER`), but the invariant tests (`tests/invariants/test_phase0.py`) assert `aiosqlite.OperationalError`. `IntegrityError` semantically means constraint violation (FK, CHECK, UNIQUE), which isn't quite what an immutability trigger is doing. The fix uses a reference to a non-existent table (`SELECT * FROM "events are immutable (rule 1)"`) which naturally produces `OperationalError` with the rule text in the message.

2. **The `WHEN OLD.type != 'halted'` guard on the update trigger was speculative.** It anticipated Phase 3's halt mechanism needing to update events, but halt events are events — they're inserted, never updated. The guard created an exception to rule 1 with no consumer, weakening the invariant. Removed.

Both changes accepted as improvements over the blueprint spec.
