# Blueprint Drift Log

Known deviations between the blueprint documents in `docs/blueprint/` and the implementation in `src/`. Each entry is raised here first, then either resolved (reconcile code to blueprint), accepted (update blueprint), or deferred (explicit decision to diverge, with reason).

Entries are append-only. Once resolved, the entry stays with its resolution noted.

| Date | Area | Divergence | Resolution | Status |
|---|---|---|---|---|
| 2026-04-16 | migration/phase0 | Removed `WHEN OLD.type != 'halted'` guard on `events_immutable_update` trigger; changed trigger mechanism from `RAISE(ABORT, ...)` to non-existent-table reference to produce `OperationalError` instead of `IntegrityError` | Accepted — guard was speculative (halt events are inserted, never updated) and weakened rule 1 by creating an exception with no consumer; error type change aligns with what invariant tests check, and `OperationalError` is semantically more correct than `IntegrityError` for an immutability enforcement | Resolved |
| 2026-04-16 | retrieval/phase1 | BM25 score filter changed from `> 0.0` to `!= 0.0` to handle rank-bm25 negative IDF in small corpora | Accepted — BM25Okapi produces negative IDF scores when corpus has < 3 documents, which made temporal as_of queries (where the matching corpus is often 1-2 documents) return empty; non-matching docs always score exactly 0.0 so `!= 0.0` correctly preserves relevance signal | Resolved |
| 2026-04-16 | retrieval/phase1 | BM25 index is rebuilt from scratch on every `recall()` call instead of being cached/incremental | Accepted as Phase 1 simplification — p99 is 19ms at 10k neurons so not a bottleneck yet; incremental maintenance deferred to Phase 6 when sustained QPS matters | Open |
| 2026-04-16 | retrieval/phase1 | `emit_trace_async` task retention set was local variable (GC could collect tasks mid-execution); fixed to module-level `_background_tasks` set; also changed from `async def` to `def` since it was never awaited | Bug fix — the original pattern created `_background_tasks: set` inside the function body, so it went out of scope on return and the task strong reference was lost; under load traces would silently vanish | Resolved |
| 2026-04-16 | retrieval/phase1 | Production-realistic p99=131ms (6x headroom to 800ms budget) — two caveats: (1) Phase 2 consolidator shares CPU with query embedder; concurrent embedding will spike latency unless run_in_executor separates them, (2) grounding gate triggers internal recall calls not present in Phase 1 load; re-measure at Phase 2 close with consolidator running | Documented as baseline caveat — not a divergence, just honest context for the 6x headroom claim | Open |
| 2026-04-16 | retrieval/phase1 | BM25 and query embedding could run concurrently (no data dependency until fusion step) but currently run sequentially; ~10ms async overhead in the pipeline | Phase 6+ optimization opportunity — don't act now, but the lever is asyncio.gather(bm25_search, embed_query) joining at RRF | Open |

## Conventions

- Raise a drift entry the same commit the divergence is introduced.
- Format: one-liner in the table above, full explanation in a subsection below.
- When resolving, update the Status column but do not edit the original entry text.

## Open entries

### 2026-04-16: BM25 index rebuild-per-call (retrieval bm25.py)

`BM25Index.build()` is called fresh on every `recall()` invocation — it loads all matching neurons from SQLite into memory and constructs a new `BM25Okapi` index. At 10k neurons this costs ~10ms (the dominant cost in a BM25-only query), which is fine for Phase 1's single-user, low-QPS scenario.

At sustained 100 QPS this becomes 100 rebuilds/s. At 100k+ neurons the rebuild time will be linear and start to matter. The cache isn't warm across queries — IDF tables and tokenized corpus are recomputed each time.

Deferred to Phase 6 (observability/operational hardening). The fix is a per-persona memoized BM25 index that invalidates on neuron insert/supersede/prune events.

## Resolved entries

### 2026-04-16: Immutability trigger mechanism (migration 001)

The Phase 0 doc specified `RAISE(ABORT, 'events are immutable (rule 1)')` in the immutability triggers. Two issues:

1. **`RAISE(ABORT, ...)` in SQLite triggers produces `IntegrityError` in Python's sqlite3 module** (mapped from `SQLITE_CONSTRAINT_TRIGGER`), but the invariant tests (`tests/invariants/test_phase0.py`) assert `aiosqlite.OperationalError`. `IntegrityError` semantically means constraint violation (FK, CHECK, UNIQUE), which isn't quite what an immutability trigger is doing. The fix uses a reference to a non-existent table (`SELECT * FROM "events are immutable (rule 1)"`) which naturally produces `OperationalError` with the rule text in the message.

2. **The `WHEN OLD.type != 'halted'` guard on the update trigger was speculative.** It anticipated Phase 3's halt mechanism needing to update events, but halt events are events — they're inserted, never updated. The guard created an exception to rule 1 with no consumer, weakening the invariant. Removed.

Both changes accepted as improvements over the blueprint spec.

### 2026-04-16: BM25 negative IDF in small corpora (retrieval bm25.py)

`rank_bm25.BM25Okapi` computes IDF as `log((N - df + 0.5) / (df + 0.5))`. When the corpus has fewer than ~3 documents, every matching term has `df ≈ N`, making the IDF expression `log(<1)` = negative. This means a document with perfect token overlap scores negative, and the original `scores[i] > 0.0` filter discarded it as irrelevant.

This surfaces in `as_of` temporal queries where the lens + temporal filter narrows the corpus to 1-2 documents (e.g., querying a superseded neuron at a point in time where it was the only match). Non-matching documents always score exactly `0.0`, so changing the filter to `!= 0.0` correctly preserves the relevance signal without introducing false positives.

The blueprint spec doesn't prescribe BM25 implementation details, so this is an implementation decision rather than a blueprint divergence. Accepted.

### 2026-04-16: Fire-and-forget task retention bug (retrieval trace.py)

`emit_trace_async()` used `asyncio.create_task()` with the task-retention `_background_tasks` set, but the set was declared as a **local variable** inside the function body. When the function returned, the set went out of scope. Python's GC could then collect the set and with it the only strong reference to the task, causing the task to be silently cancelled mid-execution.

Under low load (tests, single queries) this would rarely manifest because the task typically completes before GC runs. Under sustained load (75+ QPS), the probability of a task being collected mid-write increases, causing retrieval traces to silently vanish. There's no error, no log — the task just disappears.

Fix: hoist `_background_tasks` to module level. Also changed `emit_trace_async` from `async def` to `def` since it was never awaited — the function schedules work and returns synchronously, which is clearer in the type signature.
