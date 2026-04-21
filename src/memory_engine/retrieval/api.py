"""Top-level recall() function — the retrieval API.

Pure read. Runs BM25 + vector + graph streams, fuses with RRF,
loads full neuron data with citations, and returns results.
Emits a retrieval_trace asynchronously (rule 7).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from memory_engine.retrieval.bm25 import bm25_search
from memory_engine.retrieval.fuse import fuse_rrf
from memory_engine.retrieval.graph import graph_search
from memory_engine.retrieval.lens import parse_lens
from memory_engine.retrieval.models import Citation, Neuron, RecallResult, RecallScores
from memory_engine.retrieval.vector import vector_search

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

# Rough token estimate: 1 token ~= 4 chars
_CHARS_PER_TOKEN = 4


async def recall(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    query: str,
    lens: str = "auto",
    as_of: datetime | None = None,
    top_k: int = 10,
    token_budget: int | None = None,
    query_embedding: list[float] | None = None,
    embedder_rev: str | None = None,
) -> list[RecallResult]:
    """Retrieve the top-k most relevant neurons for a query under a lens.

    Pure read. Does not mutate neurons or synapses.

    Args:
        conn: DB connection.
        persona_id: Which persona's memory to query.
        query: The question or topic in natural language.
        lens: 'auto', 'self', 'counterparty:<external_ref>', or 'domain'.
        as_of: Point-in-time query. None = current state.
        top_k: Number of results.
        token_budget: If set, truncate results to fit within this many tokens.
        query_embedding: Pre-computed query embedding. If None, vector stream is skipped.
        embedder_rev: Embedder revision for vector filtering. Required if query_embedding is set.

    Returns:
        List of RecallResult with neurons, citations, and scores.
    """
    if not query:
        return []

    lens_filter = parse_lens(lens, persona_id)
    start = time.monotonic()

    # Format as_of for SQL
    as_of_str: str | None = None
    if as_of is not None:
        as_of_str = as_of.strftime("%Y-%m-%d %H:%M:%S")

    # Run BM25 stream
    bm25_results = await bm25_search(
        conn, persona_id, query, lens_filter, top_k=50, as_of=as_of_str
    )

    # Run vector stream (if embedding provided)
    vector_results: list[tuple[int, float]] = []
    if query_embedding is not None and embedder_rev is not None:
        vector_results = await vector_search(
            conn,
            persona_id,
            query_embedding,
            embedder_rev,
            lens_filter,
            top_k=50,
            as_of=as_of_str,
        )

    # Run graph stream (empty in Phase 1)
    seed_ids = [nid for nid, _ in bm25_results[:10]]
    graph_results = await graph_search(conn, persona_id, seed_ids, lens_filter)

    # Build score lookups for per-neuron scoring
    bm25_scores: dict[int, float] = dict(bm25_results)
    vector_scores: dict[int, float] = dict(vector_results)
    graph_scores: dict[int, float] = dict(graph_results)

    # RRF fusion
    rankings: dict[str, list[tuple[int, float]]] = {"bm25": bm25_results}
    if vector_results:
        rankings["vector"] = vector_results
    if graph_results:
        rankings["graph"] = graph_results

    fused = fuse_rrf(rankings, k=60, top_k=top_k)

    # Load full neuron data for fused results
    results: list[RecallResult] = []
    for neuron_id, fused_score, rank_sources in fused:
        neuron = await _load_neuron(conn, neuron_id, as_of=as_of)
        if neuron is None:
            continue
        citations = await _load_citations(conn, neuron_id)
        scores = RecallScores(
            bm25=bm25_scores.get(neuron_id, 0.0),
            vector=vector_scores.get(neuron_id, 0.0),
            graph=graph_scores.get(neuron_id, 0.0),
            fused=fused_score,
            rank_sources=rank_sources,
        )
        results.append(RecallResult(neuron=neuron, citations=citations, scores=scores))

    # Apply token budget truncation
    if token_budget is not None:
        results = _truncate_by_budget(results, token_budget)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.debug("recall: %d results in %dms (lens=%s)", len(results), elapsed_ms, lens)

    return results


def _truncate_by_budget(results: list[RecallResult], budget: int) -> list[RecallResult]:
    """Truncate results to fit within token budget."""
    truncated: list[RecallResult] = []
    used = 0
    for r in results:
        tokens_est = len(r.neuron.content) // _CHARS_PER_TOKEN + 1
        if used + tokens_est > budget and truncated:
            break
        truncated.append(r)
        used += tokens_est
    return truncated


async def _load_neuron(
    conn: aiosqlite.Connection,
    neuron_id: int,
    as_of: datetime | None = None,
) -> Neuron | None:
    """Load a single neuron by ID, respecting as_of for supersession."""
    if as_of is not None:
        # As-of query: find the neuron version active at that time
        as_of_str = as_of.strftime("%Y-%m-%d %H:%M:%S")
        cursor = await conn.execute(
            """
            SELECT id, persona_id, counterparty_id, kind, content, tier,
                   t_valid_start, t_valid_end, recorded_at, distinct_source_count,
                   embedder_rev
            FROM neurons
            WHERE id = ? AND recorded_at <= ?
              AND (superseded_at IS NULL OR superseded_at > ?)
            """,
            (neuron_id, as_of_str, as_of_str),
        )
    else:
        cursor = await conn.execute(
            """
            SELECT id, persona_id, counterparty_id, kind, content, tier,
                   t_valid_start, t_valid_end, recorded_at, distinct_source_count,
                   embedder_rev
            FROM neurons WHERE id = ? AND superseded_at IS NULL
            """,
            (neuron_id,),
        )

    row = await cursor.fetchone()
    if row is None:
        return None

    return Neuron(
        id=row["id"],
        persona_id=row["persona_id"],
        counterparty_id=row["counterparty_id"],
        kind=row["kind"],
        content=row["content"],
        tier=row["tier"],
        t_valid_start=_parse_dt(row["t_valid_start"]),
        t_valid_end=_parse_dt(row["t_valid_end"]),
        recorded_at=datetime.fromisoformat(row["recorded_at"]).replace(tzinfo=UTC),
        distinct_source_count=row["distinct_source_count"],
        embedder_rev=row["embedder_rev"],
    )


async def _load_citations(
    conn: aiosqlite.Connection,
    neuron_id: int,
) -> tuple[Citation, ...]:
    """Load citations for a neuron from its source_event_ids."""
    cursor = await conn.execute(
        "SELECT source_event_ids FROM neurons WHERE id = ?",
        (neuron_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return ()

    event_ids: list[int] = json.loads(row["source_event_ids"])
    citations: list[Citation] = []
    for eid in event_ids:
        ecursor = await conn.execute(
            "SELECT id, recorded_at, content_hash FROM events WHERE id = ?",
            (eid,),
        )
        erow = await ecursor.fetchone()
        if erow is not None:
            citations.append(
                Citation(
                    event_id=erow["id"],
                    recorded_at=datetime.fromisoformat(erow["recorded_at"]).replace(tzinfo=UTC),
                    content_hash=erow["content_hash"],
                )
            )

    return tuple(citations)


def _parse_dt(val: str | None) -> datetime | None:
    if val is None:
        return None
    return datetime.fromisoformat(val).replace(tzinfo=UTC)
