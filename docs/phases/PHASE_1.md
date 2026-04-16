# Phase 1 — Retrieval

> **Status:** Blocked on Phase 0 completion.
>
> **Duration:** 3 weeks (half-time solo).
>
> **Acceptance criterion:** On a seeded 1000-neuron fixture across three counterparties, the `counterparty:alice` lens returns exactly the Alice neurons and domain neurons — never a Bob neuron. MRR@10 on the eval baseline fixtures exceeds 0.60.

---

## Goal

A query returns relevant neurons with citations. Pure read. Three retrieval streams — BM25, vector, graph — fused by reciprocal rank fusion, then filtered by lens. Retrieval never writes synchronously (rule 7); it emits a `retrieval_trace` event asynchronously for the consolidator to pick up later.

Phase 1 does not add LLMs. The auto-lens classifier uses a tiny local model (Phase 2 introduces the policy plane; Phase 1 can hard-code lens from an API parameter).

---

## Prerequisites

- Phase 0 complete. Event log works. Schema in place.
- A seeding script for neurons exists (even if consolidation doesn't). Tests need to populate `neurons` directly for Phase 1 development.

---

## Schema changes

No new migration. Phase 0's `neurons` and `neurons_vec` tables suffice. Phase 1 populates them via test fixtures and a seed CLI command.

Add a CLI helper:

```bash
memory-engine seed-neurons --fixture phase1_baseline.yaml
```

This loads a YAML of neurons for manual testing and the eval baseline.

---

## File manifest

### New source files

- `src/memory_engine/retrieval/__init__.py` — expose `recall()` and `Lens`.
- `src/memory_engine/retrieval/api.py` — top-level `recall()` function.
- `src/memory_engine/retrieval/bm25.py` — rank-bm25 wrapper with per-persona index.
- `src/memory_engine/retrieval/vector.py` — sqlite-vec query wrapper.
- `src/memory_engine/retrieval/graph.py` — synapse walk for graph stream.
- `src/memory_engine/retrieval/fuse.py` — reciprocal rank fusion.
- `src/memory_engine/retrieval/lens.py` — lens parsing and SQL WHERE generation.
- `src/memory_engine/retrieval/trace.py` — async retrieval_trace emission.
- `src/memory_engine/retrieval/models.py` — `Neuron`, `Recall Result`, `Citation` dataclasses.

### New HTTP surface

- `src/memory_engine/http/__init__.py`
- `src/memory_engine/http/app.py` — FastAPI application.
- `src/memory_engine/http/routes/recall.py` — `POST /v1/recall`.

### CLI additions

- `src/memory_engine/cli/serve.py` — `memory-engine serve`.
- `src/memory_engine/cli/seed.py` — `memory-engine seed-neurons`.

### Tests

- `tests/integration/test_phase1.py` — retrieval correctness across streams and lenses.
- `tests/invariants/test_phase1_retrieval.py` — rules 7 and 12.
- `tests/fixtures/neurons.py` — factory for seeded neurons across counterparties.
- `tests/fixtures/phase1_baseline.yaml` — eval baseline seed data.

---

## Core API

### `recall()` signature

```python
async def recall(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    query: str,
    lens: str = "auto",
    as_of: datetime | None = None,
    top_k: int = 10,
    token_budget: int | None = None,
) -> list[RecallResult]:
    """Retrieve the top-k most relevant neurons for a query under a lens.

    Pure read. Emits a retrieval_trace event asynchronously (fire and forget).

    Args:
        conn: DB connection.
        persona_id: Which persona's memory to query.
        query: The question or topic in natural language.
        lens: 'auto', 'self', 'counterparty:<external_ref>', or 'domain'.
            'auto' picks a lens based on the query; Phase 1 defaults to 'self'
            until the policy plane exists.
        as_of: Point-in-time query. Returns neuron state as it was at that time.
            None = current state.
        top_k: Number of results.
        token_budget: If set, truncate results to fit within roughly this many
            tokens when assembled into a context. Overrides top_k if smaller.

    Returns:
        List of RecallResult, each containing a Neuron and its citations
        (source event IDs).
    """
```

### Data shapes

```python
@dataclass(frozen=True, slots=True)
class Neuron:
    id: int
    persona_id: int
    counterparty_id: int | None
    kind: str
    content: str
    tier: str
    t_valid_start: datetime | None
    t_valid_end: datetime | None
    recorded_at: datetime
    distinct_source_count: int
    embedder_rev: str


@dataclass(frozen=True, slots=True)
class Citation:
    event_id: int
    recorded_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class RecallResult:
    neuron: Neuron
    citations: tuple[Citation, ...]
    scores: RecallScores  # bm25, vector, graph, fused


@dataclass(frozen=True, slots=True)
class RecallScores:
    bm25: float
    vector: float
    graph: float
    fused: float
    rank_sources: tuple[str, ...]  # which streams contributed to the fused rank
```

---

## Retrieval streams

### BM25

Build a rank-bm25 index per persona, held in memory and rebuilt when neurons change (Phase 1: rebuild on every 100 neuron insertions or on startup; Phase 3 adds a smarter invalidation strategy). BM25 tokenizer: lowercase, strip punctuation, no stemming (keeps multilingual content workable).

```python
async def bm25_search(
    conn: aiosqlite.Connection,
    persona_id: int,
    query: str,
    lens_where: str,
    lens_params: tuple,
    top_k: int = 50,      # wider than final k; fused rank cuts to k
) -> list[tuple[int, float]]:
    """Return [(neuron_id, bm25_score)] sorted by score desc."""
```

### Vector

Embed the query with the same model used for neurons. Query `neurons_vec` with cosine similarity. Filter by `embedder_rev` — only compare against neurons with matching revision. Neurons from an old revision stay queryable until re-embedded (see Gap 6 in synthesis).

```python
async def vector_search(
    conn: aiosqlite.Connection,
    persona_id: int,
    query_embedding: list[float],
    embedder_rev: str,
    lens_where: str,
    lens_params: tuple,
    top_k: int = 50,
) -> list[tuple[int, float]]:
    """Return [(neuron_id, cosine_similarity)] sorted by similarity desc."""
```

### Graph

Walk the synapse graph from seeds. Phase 1 uses a simple seeding strategy: take the top-10 BM25 results as seeds, walk outgoing synapses with weight > 0.5 up to 2 hops, score each target by `seed_score * edge_weight / hop_depth`. Phase 3 can refine.

```python
async def graph_search(
    conn: aiosqlite.Connection,
    persona_id: int,
    seed_neuron_ids: list[int],
    lens_where: str,
    lens_params: tuple,
    max_hops: int = 2,
    top_k: int = 50,
) -> list[tuple[int, float]]:
    """Return [(neuron_id, graph_score)] sorted by score desc."""
```

Synapses table doesn't exist until Phase 3. Phase 1's graph stream returns an empty list; fusion degrades gracefully to BM25+vector.

---

## Lens enforcement

The lens parameter becomes a SQL `WHERE` clause. This is the critical boundary — rule 12 says cross-counterparty retrieval is structurally forbidden in the normal API.

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class LensFilter:
    where_clause: str
    params: tuple

def parse_lens(lens: str, persona_id: int) -> LensFilter:
    """Translate a lens string into a SQL WHERE filter.

    Lens grammar:
      - 'self'              → self_facts only
      - 'counterparty:<ref>' → counterparty_facts for <ref> + domain_facts
      - 'domain'            → domain_facts only
      - 'auto'              → defaults to 'self' in Phase 1
                              Phase 2+ uses the policy plane's classifier

    The returned WHERE clause is AND-appended to whatever outer query runs.
    Always scoped to persona_id.

    Raises:
        ValueError: Unknown lens.
    """
    if lens == "self":
        return LensFilter(
            where_clause="(persona_id = ? AND kind = 'self_fact')",
            params=(persona_id,),
        )
    if lens == "domain":
        return LensFilter(
            where_clause="(persona_id = ? AND kind = 'domain_fact')",
            params=(persona_id,),
        )
    if lens.startswith("counterparty:"):
        external_ref = lens.split(":", 1)[1]
        return LensFilter(
            where_clause=(
                "(persona_id = ? AND ("
                "  (kind = 'counterparty_fact' AND counterparty_id = "
                "    (SELECT id FROM counterparties WHERE persona_id = ? AND external_ref = ?))"
                "  OR kind = 'domain_fact'"
                "))"
            ),
            params=(persona_id, persona_id, external_ref),
        )
    if lens == "auto":
        return parse_lens("self", persona_id)  # Phase 1 default
    raise ValueError(f"Unknown lens: {lens}")
```

Every retrieval stream applies this filter. No way to bypass; no code path exists that skips it. If you find one, it's a rule 12 violation.

---

## Reciprocal Rank Fusion

```python
def fuse_rrf(
    rankings: dict[str, list[tuple[int, float]]],
    k: int = 60,
    top_k: int = 10,
) -> list[tuple[int, float, tuple[str, ...]]]:
    """Reciprocal rank fusion of multiple ranked lists.

    Args:
        rankings: stream_name -> [(id, score)] sorted by score desc
        k: RRF damping. Standard is 60.
        top_k: Final result count.

    Returns:
        [(id, fused_score, contributing_streams)] sorted by fused_score desc.
    """
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for stream, ranked in rankings.items():
        for rank, (neuron_id, _score) in enumerate(ranked, start=1):
            scores[neuron_id] = scores.get(neuron_id, 0.0) + 1.0 / (k + rank)
            sources.setdefault(neuron_id, []).append(stream)
    sorted_ids = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [(nid, score, tuple(sources[nid])) for nid, score in sorted_ids]
```

---

## Retrieval trace

Every `recall()` emits a `retrieval_trace` event async. This is a *write* but it happens *after* results are returned and does not block the caller.

```python
async def emit_trace_async(
    conn_factory: Callable[[], Awaitable[aiosqlite.Connection]],
    persona_id: int,
    query: str,
    lens: str,
    top_neuron_ids: list[int],
    latency_ms: int,
) -> None:
    """Enqueue a retrieval_trace event. Does not block the caller.

    In Phase 1 this uses asyncio.create_task on a fresh connection.
    In Phase 6 (observability) this goes through a bounded queue with
    backpressure.
    """
```

The consolidator (Phase 2) reads these events to apply LTP reinforcement.

---

## HTTP API

### `POST /v1/recall`

Request:

```json
{
  "persona_slug": "sales_twin",
  "query": "what did alice say about pricing?",
  "lens": "counterparty:whatsapp:+94771234567",
  "top_k": 5,
  "token_budget": 1000
}
```

Response:

```json
{
  "results": [
    {
      "neuron_id": 1243,
      "content": "Alice asked about the 2025 pricing tier on Oct 3",
      "tier": "episodic",
      "citations": [
        {"event_id": 98211, "recorded_at": "2025-10-03T14:22:00Z"}
      ],
      "scores": {"bm25": 3.21, "vector": 0.82, "graph": 0.0, "fused": 0.0328},
      "rank_sources": ["bm25", "vector"]
    }
  ],
  "latency_ms": 142,
  "lens_applied": "counterparty:whatsapp:+94771234567"
}
```

---

## Tests

### Integration (tests/integration/test_phase1.py)

```
test_recall_returns_results_for_seeded_query
test_bm25_finds_exact_term_match
test_vector_finds_semantic_match
test_rrf_blends_bm25_and_vector
test_lens_self_returns_only_self_facts
test_lens_counterparty_returns_counterparty_and_domain
test_lens_domain_excludes_counterparty_facts
test_as_of_returns_state_at_past_time            # uses superseded_at
test_retrieval_emits_trace_event_async
test_token_budget_truncates_results
test_empty_query_returns_empty                   # not an error
test_unknown_lens_raises
```

### Invariants (tests/invariants/test_phase1_retrieval.py)

```
test_recall_never_writes_neurons_synchronously    # Rule 7
test_cross_counterparty_lens_cannot_leak_across   # Rule 12
test_T3_ingest_recall_isolation                   # from adapter spec
test_retrieval_trace_event_content_hash_stable
```

### Eval baseline (tests/eval/test_recall_baseline.py)

```python
@pytest.mark.eval
async def test_mrr_at_10_above_0_6() -> None:
    """Seeded baseline must achieve MRR@10 > 0.6. Phase 1 acceptance."""
    # Load tests/fixtures/phase1_baseline.yaml: queries + expected top neuron IDs
    # Run recall for each, compute MRR@10
    # Assert against threshold
```

Baseline fixture: 50 queries with hand-curated expected neurons. Stored in `tests/fixtures/phase1_baseline.yaml` with entries like:

```yaml
- query: "what is alice's preferred meeting time"
  lens: "counterparty:whatsapp:+94771234567"
  expected_neuron_ids: [1243, 1244, 1290]  # in rank order
```

---

## Performance targets

- Recall p50 latency: < 150ms for top_k=10 on 10k neurons per persona.
- Recall p99 latency: < 800ms same shape.
- BM25 index rebuild: < 500ms for 10k neurons.
- Vector query: < 50ms on 10k-row `neurons_vec`.

Miss these and Phase 1 has a performance issue to resolve before Phase 2. Phase 2's consolidator will add write pressure; if retrieval is already slow, adding writes will make p99 unacceptable.

---

## Out of scope for this phase

- LLM-based auto-lens classification (Phase 2).
- Query decomposition (backlog — see ElBruno pattern in synthesis).
- Reranking with a cross-encoder (Phase 2+).
- Retrieval trace → reinforcement loop (Phase 2).
- Synapse graph population (Phase 3; graph stream returns empty).
- as-of queries beyond a single column check on `superseded_at` (Phase 2 adds bi-temporal filters for `t_valid_*`).
- Observability dashboards (Phase 6).

---

## Common pitfalls

**Lens bypass via SQL string concat.** If you find yourself building the WHERE clause by string concatenation with the lens value, stop. `parse_lens()` returns parameterized SQL only. Any bypass is a rule 12 violation.

**Empty-stream handling.** If vector search finds zero results (bad embedder, empty DB), RRF with only BM25 must still return results. Do not raise; return what's available.

**Embedder revision mismatch.** Neurons with a different `embedder_rev` should not participate in vector search. Filter by rev. Phase 1's eval baseline must all share one revision; mixed revisions test scenarios belong in Phase 6 when rotation is specified.

**Retrieval trace event flood.** Every recall emits an event. A chatty agent can write thousands of traces per minute. Phase 1 is OK with unbounded growth; Phase 6 introduces batching. If trace volume becomes unmanageable during Phase 1 testing, raise the issue — don't paper over with a silent drop.

**Case sensitivity in BM25.** Tokens must be lowercased consistently at index time and query time. A query for "Alice" that doesn't match "alice" in the neuron content is a bug.

**Top-k ordering across streams.** Each stream returns top-50 internally; fusion cuts to top-k. If a stream returns more than 50 because of a tie, truncate deterministically (by neuron_id ascending) so RRF output is stable across runs.

---

## When Phase 1 closes

Tag: `git tag phase-1-complete`. Update `CLAUDE.md` §8. Open `PHASE_2.md`.

Commit message: `feat(phase1): hybrid recall with lens enforcement passes acceptance`.
