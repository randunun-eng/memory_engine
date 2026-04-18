"""BM25 retrieval stream.

Maintains a per-persona in-memory BM25 index built from active neuron content.
Rebuilt on startup and periodically (every 60s or 100 inserts, whichever first).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from rank_bm25 import BM25Plus

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.retrieval.lens import LensFilter

logger = logging.getLogger(__name__)

# Simple tokenizer: lowercase, split on non-alphanumeric, drop short tokens.
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.ASCII | re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Lowercase tokenize for BM25. No stemming (keeps multilingual workable)."""
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]


class BM25Index:
    """In-memory BM25 index for one persona's active neurons."""

    def __init__(self) -> None:
        self._neuron_ids: list[int] = []
        self._index: BM25Plus | None = None

    @property
    def size(self) -> int:
        return len(self._neuron_ids)

    async def build(
        self,
        conn: aiosqlite.Connection,
        persona_id: int,
        lens_filter: LensFilter,
        as_of: str | None = None,
    ) -> None:
        """Build index from neurons matching the lens, respecting as_of."""
        if as_of is not None:
            temporal = "n.recorded_at <= ? AND (n.superseded_at IS NULL OR n.superseded_at > ?)"
            query = (
                "SELECT n.id, n.content FROM neurons n "
                f"WHERE {lens_filter.where_clause} AND {temporal} "
                "ORDER BY n.id"
            )
            cursor = await conn.execute(query, (*lens_filter.params, as_of, as_of))
        else:
            query = (
                "SELECT n.id, n.content FROM neurons n "
                f"WHERE {lens_filter.where_clause} AND n.superseded_at IS NULL "
                "ORDER BY n.id"
            )
            cursor = await conn.execute(query, lens_filter.params)
        rows = await cursor.fetchall()

        self._neuron_ids = [row["id"] for row in rows]
        corpus = [tokenize(row["content"]) for row in rows]

        if corpus:
            self._index = BM25Plus(corpus)
        else:
            self._index = None

    def search(self, query: str, top_k: int = 50) -> list[tuple[int, float]]:
        """Return [(neuron_id, bm25_score)] sorted by score desc."""
        if self._index is None or not self._neuron_ids:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self._index.get_scores(tokens)
        # Pair with neuron IDs, keep any non-zero score. BM25Plus produces
        # negative IDF (and therefore negative scores) when the corpus has
        # fewer than ~3 documents — those negatives still indicate token
        # overlap and must be kept.  Non-matching docs always score exactly 0.
        scored = [
            (self._neuron_ids[i], float(scores[i]))
            for i in range(len(self._neuron_ids))
            if scores[i] != 0.0
        ]
        scored.sort(key=lambda x: (-x[1], x[0]))  # stable: score desc, id asc
        return scored[:top_k]


async def bm25_search(
    conn: aiosqlite.Connection,
    persona_id: int,
    query: str,
    lens_filter: LensFilter,
    top_k: int = 50,
    as_of: str | None = None,
) -> list[tuple[int, float]]:
    """Build a fresh BM25 index and search. Returns [(neuron_id, score)]."""
    index = BM25Index()
    await index.build(conn, persona_id, lens_filter, as_of=as_of)
    return index.search(query, top_k)
