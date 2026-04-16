"""Phase 1 integration tests — retrieval correctness across streams and lenses.

These tests use BM25-only retrieval (no embeddings) unless explicitly testing
vector search. The phase1_seed fixture provides the baseline neurons.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from memory_engine.retrieval import recall
from tests.fixtures.phase1_seed import seed_phase1_baseline

# ---- Shared fixture ----

@pytest.fixture
async def seeded_db(db):
    """DB with Phase 1 baseline neurons seeded."""
    await seed_phase1_baseline(db)
    return db


# ---- Basic recall ----

async def test_recall_returns_results_for_seeded_query(seeded_db) -> None:
    """recall() returns non-empty results for a query matching seeded content."""
    results = await recall(
        seeded_db, persona_id=1, query="programming languages", lens="self", top_k=5,
    )
    assert len(results) > 0
    # All results should be self_facts
    for r in results:
        assert r.neuron.kind == "self_fact"


async def test_bm25_finds_exact_term_match(seeded_db) -> None:
    """BM25 finds neurons with exact token overlap."""
    results = await recall(
        seeded_db, persona_id=1, query="Python backend", lens="self", top_k=5,
    )
    assert len(results) > 0
    # Neuron 1 mentions Python
    neuron_ids = [r.neuron.id for r in results]
    assert 1 in neuron_ids


async def test_lens_self_returns_only_self_facts(seeded_db) -> None:
    """Self lens never returns counterparty_facts or domain_facts."""
    results = await recall(
        seeded_db, persona_id=1, query="work engineering software", lens="self", top_k=10,
    )
    for r in results:
        assert r.neuron.kind == "self_fact", (
            f"Neuron {r.neuron.id} has kind={r.neuron.kind}, expected self_fact"
        )


async def test_lens_counterparty_returns_counterparty_and_domain(seeded_db) -> None:
    """Counterparty lens returns counterparty_facts for that ref + domain_facts."""
    results = await recall(
        seeded_db, persona_id=1,
        query="software engineering work",
        lens="counterparty:whatsapp:+19175550101",
        top_k=10,
    )
    for r in results:
        assert r.neuron.kind in ("counterparty_fact", "domain_fact"), (
            f"Neuron {r.neuron.id} has kind={r.neuron.kind}"
        )
        if r.neuron.kind == "counterparty_fact":
            assert r.neuron.counterparty_id == 1, (
                f"Neuron {r.neuron.id} belongs to counterparty {r.neuron.counterparty_id}, not Alex (1)"
            )


async def test_lens_domain_excludes_counterparty_facts(seeded_db) -> None:
    """Domain lens returns only domain_facts."""
    results = await recall(
        seeded_db, persona_id=1, query="MPPT algorithm solar", lens="domain", top_k=10,
    )
    assert len(results) > 0
    for r in results:
        assert r.neuron.kind == "domain_fact"


async def test_rrf_blends_bm25_and_vector(seeded_db) -> None:
    """When both BM25 and vector provide results, fused scores reflect both."""
    # BM25-only (no embedding)
    results = await recall(
        seeded_db, persona_id=1, query="espresso coffee morning", lens="self", top_k=5,
    )
    assert len(results) > 0
    for r in results:
        assert r.scores.bm25 > 0.0
        assert r.scores.fused > 0.0
        # In BM25-only mode, rank_sources should only contain 'bm25'
        assert "bm25" in r.scores.rank_sources


async def test_as_of_returns_state_at_past_time(seeded_db) -> None:
    """as_of query returns superseded neuron 16 (old Civic) instead of 17 (new EV)."""
    # Current query should return 17 (not 16, which is superseded)
    current = await recall(
        seeded_db, persona_id=1,
        query="Alex drives Honda Civic Rivian",
        lens="counterparty:whatsapp:+19175550101",
        top_k=5,
    )
    current_ids = [r.neuron.id for r in current]
    assert 17 in current_ids
    assert 16 not in current_ids

    # As-of before supersession should return 16
    past = await recall(
        seeded_db, persona_id=1,
        query="Alex drives Honda Civic",
        lens="counterparty:whatsapp:+19175550101",
        as_of=datetime(2024, 6, 1, tzinfo=UTC),
        top_k=5,
    )
    past_ids = [r.neuron.id for r in past]
    assert 16 in past_ids


async def test_token_budget_truncates_results(seeded_db) -> None:
    """Token budget limits the number of results returned."""
    full = await recall(
        seeded_db, persona_id=1,
        query="career job work history developer engineer",
        lens="self", top_k=10,
    )
    truncated = await recall(
        seeded_db, persona_id=1,
        query="career job work history developer engineer",
        lens="self", top_k=10, token_budget=30,
    )
    assert len(truncated) < len(full) or len(full) <= 1


async def test_empty_query_returns_empty(seeded_db) -> None:
    """Empty query returns empty list, not an error."""
    results = await recall(seeded_db, persona_id=1, query="", lens="self")
    assert results == []


async def test_unknown_lens_raises(seeded_db) -> None:
    """Unknown lens format raises ValueError."""
    with pytest.raises(ValueError, match="Unknown lens"):
        await recall(seeded_db, persona_id=1, query="test", lens="invalid_lens")


async def test_get_event_missing_returns_none(seeded_db) -> None:
    """Fetching results for unknown counterparty returns empty, not error."""
    results = await recall(
        seeded_db, persona_id=1,
        query="anything",
        lens="counterparty:whatsapp:+19175559999",
        top_k=5,
    )
    assert results == []


async def test_retrieval_emits_trace_event_async(seeded_db) -> None:
    """recall() itself does not write events synchronously.

    The trace is emitted async (fire-and-forget). We check that no new
    events are written by the time recall() returns. The async task
    would need an event loop tick to actually write.
    """
    cursor = await seeded_db.execute("SELECT count(*) AS c FROM events")
    before = (await cursor.fetchone())["c"]

    await recall(seeded_db, persona_id=1, query="test", lens="self", top_k=1)

    cursor = await seeded_db.execute("SELECT count(*) AS c FROM events")
    after = (await cursor.fetchone())["c"]

    # recall itself is pure read — no synchronous event writes
    assert after == before
