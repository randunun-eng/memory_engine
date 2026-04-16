"""Phase 1 invariant tests — rules 7 and 12, plus T3-early canary tests.

Rule 7:  Retrieval never writes synchronously.
Rule 12: Cross-counterparty retrieval is structurally forbidden.

T3-early: 5 cross-counterparty isolation tests that can be checked
without synapses, groups, or MCP signing. Low cost, high canary value.
"""

from __future__ import annotations

import asyncio

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


async def test_emit_trace_async_does_not_block_caller(seeded_db, monkeypatch) -> None:
    """Rule 7 (strong form): emit_trace_async returns before the trace write completes.

    Monkeypatches append_event with an artificially slow version, then
    calls emit_trace_async and checks three properties:
    - emit_trace_async returns synchronously (it's a def, not async def)
    - The write was scheduled (write_started is set after one event loop tick)
    - The write hasn't finished when the caller resumes (write_finished is NOT set)
    - The write does eventually complete (the task-retention set keeps it alive)
    """
    import time
    from unittest.mock import AsyncMock

    import memory_engine.retrieval.trace as trace_mod
    from memory_engine.retrieval.trace import _background_tasks, emit_trace_async

    write_started = asyncio.Event()
    write_finished = asyncio.Event()

    async def slow_append_event(*args, **kwargs):
        write_started.set()
        await asyncio.sleep(0.5)
        write_finished.set()

    monkeypatch.setattr(trace_mod, "append_event", slow_append_event)

    # Build a fake conn_factory that returns a mock connection
    # (the real append_event is monkeypatched, so conn is never used)
    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    async def fake_conn_factory():
        return mock_conn

    import base64

    from memory_engine.policy.signing import generate_keypair

    priv, pub = generate_keypair()
    pub_b64 = base64.b64encode(pub).decode("ascii")

    t0 = time.perf_counter()
    emit_trace_async(
        conn_factory=fake_conn_factory,
        persona_id=1,
        query="test",
        lens="self",
        top_neuron_ids=[1, 2],
        latency_ms=10,
        private_key=priv,
        public_key_b64=pub_b64,
    )
    elapsed = time.perf_counter() - t0

    # Function returned synchronously — should be near-instant
    assert elapsed < 0.05, f"emit_trace_async took {elapsed:.3f}s — not synchronous"

    # Yield to event loop so the background task can start
    await asyncio.sleep(0)

    assert write_started.is_set(), "trace was never scheduled"
    assert not write_finished.is_set(), "caller blocked until trace completed"

    # Verify the task is held by the module-level set
    assert len(_background_tasks) > 0, "no tasks in _background_tasks — GC risk"

    # Let the background task finish cleanly
    await asyncio.wait_for(write_finished.wait(), timeout=2.0)
    assert write_finished.is_set(), "background trace task never completed"


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
