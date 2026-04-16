"""Graph retrieval stream.

Phase 1: returns empty list. Synapses don't exist until Phase 3's
migration. RRF degrades gracefully to BM25+vector fusion.

Do NOT create synapse tables speculatively — empty list is the correct
Phase 1 output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.retrieval.lens import LensFilter


async def graph_search(
    conn: aiosqlite.Connection,
    persona_id: int,
    seed_neuron_ids: list[int],
    lens_filter: LensFilter,
    max_hops: int = 2,
    top_k: int = 50,
) -> list[tuple[int, float]]:
    """Return [(neuron_id, graph_score)] sorted by score desc.

    Phase 1: always returns empty list. Synapses table doesn't exist yet.
    Phase 3 will implement the actual walk: top-10 BM25 seeds -> outgoing
    synapses with weight > 0.5 -> up to max_hops -> score by
    seed_score * edge_weight / hop_depth.
    """
    return []
