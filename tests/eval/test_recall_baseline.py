"""Phase 1 eval baseline — MRR@10 on seeded fixture.

This test is slow (loads the sentence-transformers model). Run with --eval flag.
Acceptance criterion: MRR@10 > 0.60.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from memory_engine.retrieval import recall
from tests.fixtures.phase1_seed import seed_phase1_baseline

BASELINE_PATH = Path(__file__).parent.parent / "fixtures" / "phase1_baseline.yaml"


def _load_embedder():
    """Load the sentence-transformers model. Cached at module level."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def embed(text: str) -> list[float]:
        return model.encode(text, normalize_embeddings=True).tolist()

    return embed


@pytest.fixture(scope="module")
def embedder():
    """Module-scoped embedder to avoid reloading per test."""
    return _load_embedder()


@pytest.fixture
async def seeded_db_with_vectors(db, embedder):
    """DB seeded with neurons AND vector embeddings."""
    await seed_phase1_baseline(db, embed_fn=embedder)
    return db


@pytest.mark.eval
async def test_mrr_at_10_above_0_6(seeded_db_with_vectors, embedder) -> None:
    """Seeded baseline must achieve MRR@10 > 0.60. Phase 1 acceptance."""
    baseline = yaml.safe_load(BASELINE_PATH.read_text())
    queries = baseline["queries"]

    reciprocal_ranks: list[float] = []

    for entry in queries:
        query_text = entry["query"]
        lens = entry["lens"]
        expected_ids = entry["expected_neuron_ids"]
        as_of_str = entry.get("as_of")

        as_of = None
        if as_of_str:
            as_of = datetime.fromisoformat(as_of_str).replace(tzinfo=UTC)

        # Skip empty-expected queries (negative tests) for MRR
        if not expected_ids:
            continue

        query_embedding = embedder(query_text)

        results = await recall(
            seeded_db_with_vectors,
            persona_id=1,
            query=query_text,
            lens=lens,
            as_of=as_of,
            top_k=10,
            query_embedding=query_embedding,
            embedder_rev="sbert-minilm-l6-v2-1",
        )

        result_ids = [r.neuron.id for r in results]

        # Find the rank of the first expected ID in results
        rr = 0.0
        for expected_id in expected_ids:
            if expected_id in result_ids:
                rank = result_ids.index(expected_id) + 1
                rr = 1.0 / rank
                break

        reciprocal_ranks.append(rr)

    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0

    print(f"\nMRR@10 = {mrr:.4f} ({len(reciprocal_ranks)} queries)")
    for i, rr in enumerate(reciprocal_ranks):
        if rr == 0.0:
            print(f"  MISS: query #{i}")

    assert mrr > 0.60, f"MRR@10 = {mrr:.4f}, below 0.60 threshold"
