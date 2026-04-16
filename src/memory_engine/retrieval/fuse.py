"""Reciprocal Rank Fusion of multiple retrieval streams."""

from __future__ import annotations


def fuse_rrf(
    rankings: dict[str, list[tuple[int, float]]],
    k: int = 60,
    top_k: int = 10,
) -> list[tuple[int, float, tuple[str, ...]]]:
    """Reciprocal rank fusion of multiple ranked lists.

    Args:
        rankings: stream_name -> [(id, score)] sorted by score desc.
        k: RRF damping constant. Standard is 60.
        top_k: Final result count.

    Returns:
        [(id, fused_score, contributing_streams)] sorted by fused_score desc.
        Ties broken by neuron_id ascending for determinism.
    """
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}

    for stream, ranked in rankings.items():
        for rank, (neuron_id, _score) in enumerate(ranked, start=1):
            scores[neuron_id] = scores.get(neuron_id, 0.0) + 1.0 / (k + rank)
            sources.setdefault(neuron_id, []).append(stream)

    # Sort by fused score desc, then neuron_id asc for stability
    sorted_items = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_k]
    return [(nid, score, tuple(sources[nid])) for nid, score in sorted_items]
