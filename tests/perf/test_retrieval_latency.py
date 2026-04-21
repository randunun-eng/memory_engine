"""Retrieval latency benchmark at 10k neurons.

NOT a CI test — run explicitly with:
    uv run pytest tests/perf/test_retrieval_latency.py -v -s --perf

Measures p50 and p99 recall latency for both BM25-only and full hybrid
(BM25 + vector + RRF) pipelines across self, counterparty, and domain
lenses on a 10k-neuron corpus spread across 50 counterparties.

The hybrid test embeds all 10k neurons with sentence-transformers
MiniLM and stores them in neurons_vec. Query embeddings are pre-computed
so benchmark measures recall() latency, not embedding latency. Embedding
latency is reported separately.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import statistics
import time
from typing import TYPE_CHECKING

import pytest

from memory_engine.retrieval.api import recall

if TYPE_CHECKING:
    import aiosqlite

# ---------------------------------------------------------------------------
# Content generation
# ---------------------------------------------------------------------------

_SUBJECTS = [
    "Python",
    "Rust",
    "Go",
    "TypeScript",
    "Java",
    "Kotlin",
    "Swift",
    "C++",
    "machine learning",
    "deep learning",
    "reinforcement learning",
    "NLP",
    "computer vision",
    "data engineering",
    "backend systems",
    "distributed systems",
    "solar panels",
    "wind turbines",
    "battery storage",
    "EV charging",
    "cooking",
    "gardening",
    "photography",
    "hiking",
    "cycling",
    "reading",
    "databases",
    "PostgreSQL",
    "SQLite",
    "Redis",
    "MongoDB",
    "Elasticsearch",
    "Kubernetes",
    "Docker",
    "Terraform",
    "AWS",
    "GCP",
    "Azure",
]
_VERBS = [
    "works with",
    "is learning",
    "prefers",
    "recommends",
    "uses",
    "has experience in",
    "is interested in",
    "built a project using",
    "teaches",
    "wrote a blog about",
    "gave a talk on",
    "contributed to",
]
_CONTEXTS = [
    "at work",
    "for a side project",
    "during university",
    "at a hackathon",
    "for a client",
    "in production",
    "for personal use",
    "in a team of five",
    "since 2020",
    "since last year",
    "for the past decade",
    "recently",
]


def _generate_content(rng: random.Random) -> str:
    subject = rng.choice(_SUBJECTS)
    verb = rng.choice(_VERBS)
    context = rng.choice(_CONTEXTS)
    extra = rng.choice(_SUBJECTS)
    return f"{subject} {verb} {extra} {context} and finds it valuable for modern development"


# ---------------------------------------------------------------------------
# Seed 10k neurons (optionally with embeddings)
# ---------------------------------------------------------------------------


async def _seed_10k(
    conn: aiosqlite.Connection,
    *,
    embed: bool = False,
) -> dict[str, object]:
    """Seed 10k neurons across 50 counterparties under 1 persona.

    If embed=True, loads sentence-transformers and inserts real 384d
    embeddings into neurons_vec for every neuron.
    """
    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.policy.signing import (
        canonical_signing_message,
        generate_keypair,
        sign,
    )

    priv, pub = generate_keypair()
    pub_b64 = base64.b64encode(pub).decode("ascii")

    await conn.execute("INSERT INTO personas (id, slug) VALUES (1, 'perf_twin')")
    await conn.commit()

    for cp_id in range(1, 51):
        await conn.execute(
            "INSERT INTO counterparties (id, persona_id, external_ref, display_name) "
            "VALUES (?, 1, ?, ?)",
            (cp_id, f"whatsapp:+1555000{cp_id:04d}", f"Contact_{cp_id}"),
        )
    await conn.commit()

    event_ids: list[int] = []
    for i in range(100):
        payload = {"text": f"perf source {i}"}
        ch = compute_content_hash(payload)
        sig = sign(priv, canonical_signing_message(1, ch))
        ev = await append_event(
            conn,
            persona_id=1,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=sig,
            public_key_b64=pub_b64,
            idempotency_key=f"perf-{i}",
        )
        event_ids.append(ev.id)

    rng = random.Random(42)
    neuron_id = 0
    all_contents: list[tuple[int, str]] = []

    # --- 3k self_facts ---
    for _ in range(3000):
        neuron_id += 1
        content = _generate_content(rng)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        eid = rng.choice(event_ids)
        await conn.execute(
            "INSERT INTO neurons "
            "(id, persona_id, counterparty_id, kind, content, content_hash, "
            "source_event_ids, source_count, distinct_source_count, tier, "
            "recorded_at, embedder_rev) "
            "VALUES (?, 1, NULL, 'self_fact', ?, ?, ?, 1, 1, 'episodic', "
            "datetime('now'), 'sbert-minilm-l6-v2-1')",
            (neuron_id, content, content_hash, json.dumps([eid])),
        )
        all_contents.append((neuron_id, content))

    # --- 5k counterparty_facts ---
    for _ in range(5000):
        neuron_id += 1
        content = _generate_content(rng)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        cp_id = rng.randint(1, 50)
        eid = rng.choice(event_ids)
        await conn.execute(
            "INSERT INTO neurons "
            "(id, persona_id, counterparty_id, kind, content, content_hash, "
            "source_event_ids, source_count, distinct_source_count, tier, "
            "recorded_at, embedder_rev) "
            "VALUES (?, 1, ?, 'counterparty_fact', ?, ?, ?, 1, 1, 'episodic', "
            "datetime('now'), 'sbert-minilm-l6-v2-1')",
            (neuron_id, cp_id, content, content_hash, json.dumps([eid])),
        )
        all_contents.append((neuron_id, content))

    # --- 2k domain_facts ---
    for _ in range(2000):
        neuron_id += 1
        content = _generate_content(rng)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        eid = rng.choice(event_ids)
        await conn.execute(
            "INSERT INTO neurons "
            "(id, persona_id, counterparty_id, kind, content, content_hash, "
            "source_event_ids, source_count, distinct_source_count, tier, "
            "recorded_at, embedder_rev) "
            "VALUES (?, 1, NULL, 'domain_fact', ?, ?, ?, 1, 1, 'episodic', "
            "datetime('now'), 'sbert-minilm-l6-v2-1')",
            (neuron_id, content, content_hash, json.dumps([eid])),
        )
        all_contents.append((neuron_id, content))

    await conn.commit()

    # --- Embed all neurons if requested ---
    embed_time_s = 0.0
    if embed:
        from sentence_transformers import SentenceTransformer

        print("\n[perf] Loading MiniLM embedder...")
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

        texts = [c for _, c in all_contents]
        print(f"[perf] Embedding {len(texts)} neurons (batch_size=64)...")
        t0 = time.perf_counter()
        embeddings = model.encode(texts, batch_size=64, show_progress_bar=True)
        embed_time_s = time.perf_counter() - t0
        print(
            f"[perf] Embedding done in {embed_time_s:.1f}s "
            f"({len(texts) / embed_time_s:.0f} embeds/s)"
        )

        for (nid, _), emb in zip(all_contents, embeddings, strict=True):
            await conn.execute(
                "INSERT INTO neurons_vec (neuron_id, embedding) VALUES (?, ?)",
                (nid, json.dumps(emb.tolist())),
            )
        await conn.commit()

    return {
        "total": neuron_id,
        "counterparties": 50,
        "embedded": embed,
        "embed_time_s": embed_time_s,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def bm25_db(tmp_path_factory):
    """10k neurons, no embeddings."""
    from memory_engine.db.connection import connect
    from memory_engine.db.migrations import apply_all

    db_path = tmp_path_factory.mktemp("perf_bm25") / "perf.db"
    conn = await connect(str(db_path))
    await apply_all(conn)
    info = await _seed_10k(conn, embed=False)
    print(f"\n[perf/bm25] Seeded {info['total']} neurons (no embeddings)")
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
async def hybrid_db(tmp_path_factory):
    """10k neurons with real MiniLM embeddings in neurons_vec."""
    from memory_engine.db.connection import connect
    from memory_engine.db.migrations import apply_all

    db_path = tmp_path_factory.mktemp("perf_hybrid") / "perf.db"
    conn = await connect(str(db_path))
    await apply_all(conn)
    info = await _seed_10k(conn, embed=True)
    print(
        f"\n[perf/hybrid] Seeded {info['total']} neurons with embeddings "
        f"({info['embed_time_s']:.1f}s)"
    )
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
def embedder():
    """Pre-loaded MiniLM embedder for query embedding."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

_QUERIES = [
    ("Python backend development", "self"),
    ("machine learning pipeline", "self"),
    ("solar panel efficiency MPPT", "domain"),
    ("distributed systems Kubernetes", "self"),
    ("database PostgreSQL indexing", "domain"),
    ("cooking recipes gardening", "self"),
    ("Rust systems programming", "counterparty:whatsapp:+15550000001"),
    ("TypeScript React frontend", "counterparty:whatsapp:+15550000010"),
    ("deep learning computer vision", "counterparty:whatsapp:+15550000025"),
    ("AWS cloud infrastructure", "domain"),
    ("Go concurrency patterns", "self"),
    ("battery storage wind turbines", "domain"),
    ("hiking cycling photography", "self"),
    ("Redis caching Elasticsearch", "counterparty:whatsapp:+15550000042"),
    ("Docker Terraform deployment", "self"),
]

_EMBEDDER_REV = "sbert-minilm-l6-v2-1"


def _report(label: str, latencies: list[float]) -> None:
    p50 = statistics.median(latencies)
    p99 = statistics.quantiles(latencies, n=100)[98]
    print(f"\n[{label}]")
    print(f"  Queries: {len(latencies)}")
    print(f"  p50:  {p50:7.1f} ms")
    print(f"  p99:  {p99:7.1f} ms")
    print(f"  max:  {max(latencies):7.1f} ms")
    print(f"  min:  {min(latencies):7.1f} ms")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_recall_latency_10k_bm25_only(bm25_db) -> None:
    """BM25-only p50/p99 on 10k neurons."""
    latencies: list[float] = []
    await recall(bm25_db, persona_id=1, query="warmup", lens="self", top_k=10)

    for query_text, lens in _QUERIES:
        for _ in range(5):
            start = time.perf_counter()
            await recall(bm25_db, persona_id=1, query=query_text, lens=lens, top_k=10)
            latencies.append((time.perf_counter() - start) * 1000)

    _report("BM25-only @ 10k neurons", latencies)
    p99 = statistics.quantiles(latencies, n=100)[98]
    assert p99 < 500, f"BM25-only p99 {p99:.1f}ms exceeds 500ms ceiling"


@pytest.mark.perf
async def test_recall_latency_10k_hybrid(hybrid_db, embedder) -> None:
    """Full hybrid (BM25 + vector + RRF) p50/p99 on 10k neurons.

    Query embeddings are pre-computed outside the timing loop. This
    measures recall() latency, not embedding latency.
    """
    # Pre-compute all query embeddings
    query_texts = [q for q, _ in _QUERIES]
    query_embeddings = embedder.encode(query_texts).tolist()
    query_map = dict(zip(query_texts, query_embeddings, strict=True))

    # Warm up
    await recall(
        hybrid_db,
        persona_id=1,
        query="warmup",
        lens="self",
        top_k=10,
        query_embedding=query_map[query_texts[0]],
        embedder_rev=_EMBEDDER_REV,
    )

    latencies: list[float] = []
    for query_text, lens in _QUERIES:
        emb = query_map[query_text]
        for _ in range(5):
            start = time.perf_counter()
            await recall(
                hybrid_db,
                persona_id=1,
                query=query_text,
                lens=lens,
                top_k=10,
                query_embedding=emb,
                embedder_rev=_EMBEDDER_REV,
            )
            latencies.append((time.perf_counter() - start) * 1000)

    _report("Hybrid (BM25+vector+RRF) @ 10k neurons", latencies)
    p99 = statistics.quantiles(latencies, n=100)[98]
    assert p99 < 800, f"Hybrid p99 {p99:.1f}ms exceeds 800ms ceiling"


@pytest.mark.perf
async def test_recall_latency_10k_production_realistic(hybrid_db, embedder) -> None:
    """Production-realistic latency: embed query + hybrid recall, measured together.

    This is what the HTTP route actually experiences. Phase 1 shipped
    Option A (caller pre-computes embedding), so this wraps recall()
    with an embedder.encode() call to measure the combined cost.

    Reports:
    - Combined (embed + recall): what /v1/recall would see in production
    - Recall-only (pre-embedded): the retrieval sub-component
    - Embed-only: the query embedding sub-component
    """
    # Warm up embedder and recall
    embedder.encode(["warmup"])
    warmup_emb = embedder.encode(["warmup"]).tolist()[0]
    await recall(
        hybrid_db,
        persona_id=1,
        query="warmup",
        lens="self",
        top_k=10,
        query_embedding=warmup_emb,
        embedder_rev=_EMBEDDER_REV,
    )

    combined_latencies: list[float] = []
    recall_latencies: list[float] = []
    embed_latencies: list[float] = []

    for query_text, lens in _QUERIES:
        for _ in range(5):
            # Combined: embed + recall
            t_start = time.perf_counter()

            t_embed_start = time.perf_counter()
            emb = embedder.encode([query_text]).tolist()[0]
            embed_latencies.append((time.perf_counter() - t_embed_start) * 1000)

            t_recall_start = time.perf_counter()
            await recall(
                hybrid_db,
                persona_id=1,
                query=query_text,
                lens=lens,
                top_k=10,
                query_embedding=emb,
                embedder_rev=_EMBEDDER_REV,
            )
            recall_latencies.append((time.perf_counter() - t_recall_start) * 1000)

            combined_latencies.append((time.perf_counter() - t_start) * 1000)

    _report("Production-realistic (embed+recall) @ 10k", combined_latencies)
    _report("  Recall sub-component only", recall_latencies)
    _report("  Query embed sub-component only", embed_latencies)

    p99 = statistics.quantiles(combined_latencies, n=100)[98]
    assert p99 < 800, f"Production-realistic p99 {p99:.1f}ms exceeds 800ms ceiling"


@pytest.mark.perf
async def test_query_embedding_latency(embedder) -> None:
    """Measure query embedding latency in isolation (steady-state, post-warmup)."""
    query_texts = [q for q, _ in _QUERIES]

    # Warm up
    embedder.encode(["warmup"])

    latencies: list[float] = []
    for q in query_texts:
        for _ in range(3):
            start = time.perf_counter()
            embedder.encode([q])
            latencies.append((time.perf_counter() - start) * 1000)

    _report("Query embedding (MiniLM, single query, steady-state)", latencies)


@pytest.mark.perf
async def test_trace_emission_is_nonblocking(bm25_db) -> None:
    """Verify recall() returns before the retrieval_trace write completes.

    recall() emits a trace via asyncio.create_task (fire-and-forget).
    This test confirms the trace doesn't block the caller by checking
    that no new events exist immediately after recall() returns —
    the async task needs at least one event loop iteration to write.
    """
    cursor = await bm25_db.execute("SELECT count(*) AS c FROM events")
    before = (await cursor.fetchone())["c"]

    await recall(bm25_db, persona_id=1, query="trace test", lens="self", top_k=1)

    cursor = await bm25_db.execute("SELECT count(*) AS c FROM events")
    after = (await cursor.fetchone())["c"]

    # recall() is pure read — no synchronous writes
    assert after == before, (
        f"recall() wrote {after - before} events synchronously; "
        f"trace emission should be fire-and-forget"
    )
