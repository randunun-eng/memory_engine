"""Phase 1 invariant tests — rules 7 and 12, plus T3-early canary tests.

Rule 7:  Retrieval never writes synchronously.
Rule 12: Cross-counterparty retrieval is structurally forbidden.

T3-early: 5 cross-counterparty isolation tests that can be checked
without synapses, groups, or MCP signing. Low cost, high canary value.
"""

from __future__ import annotations

import pytest

from memory_engine.retrieval import recall
from tests.fixtures.phase1_seed import seed_phase1_baseline


@pytest.fixture
async def seeded_db(db):
    """DB with Phase 1 baseline neurons seeded."""
    await seed_phase1_baseline(db)
    return db


# ---- Rule 7: retrieval never writes neurons synchronously ----

async def test_recall_never_writes_neurons_synchronously(seeded_db) -> None:
    """Rule 7: recall() is pure read. No neuron mutations during recall."""
    cursor = await seeded_db.execute("SELECT count(*) AS c FROM neurons")
    before = (await cursor.fetchone())["c"]

    await recall(seeded_db, persona_id=1, query="anything", lens="self", top_k=5)
    await recall(
        seeded_db, persona_id=1, query="Alex birthday",
        lens="counterparty:whatsapp:+19175550101", top_k=5,
    )
    await recall(seeded_db, persona_id=1, query="MPPT solar", lens="domain", top_k=5)

    cursor = await seeded_db.execute("SELECT count(*) AS c FROM neurons")
    after = (await cursor.fetchone())["c"]
    assert after == before, "recall() mutated neurons — rule 7 violation"


# ---- Rule 12: cross-counterparty isolation ----

async def test_cross_counterparty_lens_cannot_leak_across(seeded_db) -> None:
    """Rule 12: counterparty:alice lens must never return Bob's neurons."""
    # Query with Alex's lens — should never see Priya's neurons
    results = await recall(
        seeded_db, persona_id=1,
        query="machine learning data science work",
        lens="counterparty:whatsapp:+19175550101",
        top_k=20,
    )
    for r in results:
        if r.neuron.kind == "counterparty_fact":
            assert r.neuron.counterparty_id == 1, (
                f"RULE 12 VIOLATION: Neuron {r.neuron.id} (counterparty_id={r.neuron.counterparty_id}) "
                f"leaked into Alex's lens"
            )


async def test_retrieval_trace_event_content_hash_stable(seeded_db) -> None:
    """Retrieval trace events, when they do get written, have stable hashes."""
    from memory_engine.core.events import compute_content_hash

    payload1 = {"query": "test", "lens": "self", "top_neuron_ids": [1, 2], "latency_ms": 10}
    payload2 = {"query": "test", "lens": "self", "top_neuron_ids": [1, 2], "latency_ms": 10}
    assert compute_content_hash(payload1) == compute_content_hash(payload2)


# ---- T3-early: cross-counterparty isolation canary tests ----

async def test_T3_counterparty_A_query_does_not_return_counterparty_B_neurons(seeded_db) -> None:
    """T3 canary: Alex query must not return Priya or Solar crew neurons."""
    results = await recall(
        seeded_db, persona_id=1,
        query="project working research team",
        lens="counterparty:whatsapp:+19175550101",
        top_k=20,
    )
    for r in results:
        if r.neuron.kind == "counterparty_fact":
            assert r.neuron.counterparty_id == 1, (
                f"T3 VIOLATION: Neuron {r.neuron.id} from counterparty {r.neuron.counterparty_id} "
                f"appeared in Alex's lens"
            )


async def test_T3_self_lens_excludes_all_counterparty_facts(seeded_db) -> None:
    """T3 canary: self lens returns zero counterparty_facts."""
    results = await recall(
        seeded_db, persona_id=1,
        query="work project team birthday",
        lens="self",
        top_k=20,
    )
    for r in results:
        assert r.neuron.kind == "self_fact", (
            f"T3 VIOLATION: Self lens returned {r.neuron.kind} neuron {r.neuron.id}"
        )


async def test_T3_domain_lens_excludes_all_counterparty_facts(seeded_db) -> None:
    """T3 canary: domain lens returns zero counterparty_facts."""
    results = await recall(
        seeded_db, persona_id=1,
        query="algorithm software vector search",
        lens="domain",
        top_k=20,
    )
    for r in results:
        assert r.neuron.kind == "domain_fact", (
            f"T3 VIOLATION: Domain lens returned {r.neuron.kind} neuron {r.neuron.id}"
        )


async def test_T3_counterparty_lens_includes_domain_facts(seeded_db) -> None:
    """T3 canary: counterparty lens includes domain_facts alongside counterparty_facts."""
    results = await recall(
        seeded_db, persona_id=1,
        query="MPPT solar charge controller algorithm",
        lens="counterparty:whatsapp-group:120363solar@g.us",
        top_k=20,
    )
    kinds = {r.neuron.kind for r in results}
    # Should include domain_facts (MPPT content matches)
    assert "domain_fact" in kinds, "Counterparty lens should include domain_facts"


async def test_T3_unknown_counterparty_returns_empty_not_error(seeded_db) -> None:
    """T3 canary: querying a non-existent counterparty returns empty, not error."""
    results = await recall(
        seeded_db, persona_id=1,
        query="anything at all",
        lens="counterparty:whatsapp:+19175559999",
        top_k=10,
    )
    assert results == []
