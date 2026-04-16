"""Vector retrieval stream via sqlite-vec.

Embeds the query with the same model used for neurons and queries
neurons_vec with cosine distance. Filters by embedder_rev to avoid
comparing vectors from different models.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.retrieval.lens import LensFilter

logger = logging.getLogger(__name__)


async def vector_search(
    conn: aiosqlite.Connection,
    persona_id: int,
    query_embedding: list[float],
    embedder_rev: str,
    lens_filter: LensFilter,
    top_k: int = 50,
    as_of: str | None = None,
) -> list[tuple[int, float]]:
    """Return [(neuron_id, cosine_similarity)] sorted by similarity desc.

    Uses sqlite-vec's vec_distance_cosine for correct similarity metric.
    Only compares against neurons with matching embedder_rev.
    """
    query_json = json.dumps(query_embedding)

    if as_of is not None:
        temporal = "AND n.recorded_at <= ? AND (n.superseded_at IS NULL OR n.superseded_at > ?)"
        sql = (
            "SELECT nv.neuron_id, vec_distance_cosine(nv.embedding, ?) AS distance "
            "FROM neurons_vec nv "
            "INNER JOIN neurons n ON n.id = nv.neuron_id "
            f"WHERE {lens_filter.where_clause} "
            f"{temporal} "
            "AND n.embedder_rev = ? "
            "ORDER BY distance ASC "
            f"LIMIT {top_k}"
        )
        params = (query_json, *lens_filter.params, as_of, as_of, embedder_rev)
    else:
        sql = (
            "SELECT nv.neuron_id, vec_distance_cosine(nv.embedding, ?) AS distance "
            "FROM neurons_vec nv "
            "INNER JOIN neurons n ON n.id = nv.neuron_id "
            f"WHERE {lens_filter.where_clause} "
            "AND n.superseded_at IS NULL "
            "AND n.embedder_rev = ? "
            "ORDER BY distance ASC "
            f"LIMIT {top_k}"
        )
        params = (query_json, *lens_filter.params, embedder_rev)

    try:
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
    except Exception:
        # sqlite-vec may not be available; degrade gracefully
        logger.debug("Vector search failed (sqlite-vec not available?)", exc_info=True)
        return []

    # Convert distance to similarity: cosine_similarity = 1 - cosine_distance
    results: list[tuple[int, float]] = []
    for row in rows:
        similarity = 1.0 - float(row["distance"])
        results.append((row["neuron_id"], similarity))

    return results
