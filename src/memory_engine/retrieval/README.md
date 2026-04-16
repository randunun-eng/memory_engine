# `memory_engine.retrieval`

Hybrid recall across BM25, vector, and graph streams, fused by RRF and filtered by lens.

## What belongs here

- `api.py` — public `recall()` function.
- `bm25.py`, `vector.py`, `graph.py` — one retrieval stream each.
- `fuse.py` — reciprocal rank fusion.
- `lens.py` — lens parsing and SQL `WHERE` clause generation. **Load-bearing for rule 12.**
- `trace.py` — async retrieval_trace event emission (fire-and-forget).
- `models.py` — `Neuron`, `RecallResult`, `Citation`, `RecallScores` dataclasses.

## What does NOT belong here

- Writes to neurons (→ `memory_engine.core` consolidator). Retrieval never writes synchronously (rule 7).
- Raw DB connection handling (→ `memory_engine.db`).
- Query classification / auto-lens (dispatched through `memory_engine.policy`).
- HTTP routes (→ `memory_engine.http`).

## Rule 12 — the WHERE clause is law

Every retrieval stream applies `LensFilter.where_clause` via parameterized SQL. There is no code path that skips the filter. If you find one, it's a bug and a governance violation.

The `parse_lens()` function is the only place that translates a lens string into SQL. Extend it to support new lenses; never inline ad-hoc filters elsewhere.

Cross-counterparty retrieval is available only through an explicit admin function (`admin_cross_counterparty_recall`) that writes an audit event. That function lives OUTSIDE this module, in `memory_engine.admin`, and is never called by the public API.

## Rule 7 — no synchronous writes

`recall()` emits a `retrieval_trace` event *after* returning results, via `asyncio.create_task`. If you find `await` on a write inside the recall path, it's a bug.

## Performance targets

- p50 latency < 150ms on 10k neurons per persona.
- p99 latency < 800ms same shape.
- BM25 index rebuild < 500ms for 10k neurons.
- Vector query < 50ms on a 10k-row `neurons_vec`.

Miss these and the consolidator's write pressure in Phase 2 will push p99 past acceptable.

## Conventions

- Each stream returns top-50 internally; `fuse_rrf()` cuts to final top-k. This is not optional — RRF needs breadth to work.
- Ties break by `neuron_id` ascending so results are stable across runs.
- BM25 tokenization: lowercase, strip punctuation, no stemming. Keeps multilingual content workable.
- Vector search filters by `embedder_rev`; mismatched revisions do not participate. See ADR 0006 for rotation.
