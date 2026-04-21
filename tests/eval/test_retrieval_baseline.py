"""P1 #4 eval baseline — MRR@10 and P@5 on the frozen seed corpus.

Loads tests/fixtures/eval_neurons.yaml into a fresh DB with multilingual
embeddings, runs each query in eval_queries.yaml through recall() with
the fused BM25+vector+graph stack, and scores against the LLM-generated
relevance labels in eval_relevance.yaml.

Reproducible; depends only on the frozen fixtures + checked-in labels.
Run with:
    uv run pytest tests/eval/test_retrieval_baseline.py --eval -v -s
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.policy.signing import canonical_signing_message, sign
from memory_engine.retrieval import recall
from tests.fixtures.personas import make_test_persona

pytestmark = pytest.mark.eval

FIXTURES = Path(__file__).parent.parent / "fixtures"
TOP_K = 10


def _load(p: Path) -> Any:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


async def _seed_corpus(db, persona, embed_fn) -> dict[str, int]:
    """Insert every fixture neuron into the DB + neurons_vec.

    Returns fixture_id → db neuron_id mapping so the relevance set can
    be translated to DB primary keys at scoring time.
    """
    # Insert counterparties that the fixtures reference. Corpus v2 uses cp ids 1-7.
    for cp_id in range(1, 8):
        await db.execute(
            "INSERT OR IGNORE INTO counterparties (id, persona_id, external_ref) VALUES (?, ?, ?)",
            (cp_id, persona.id, f"whatsapp:+fixture{cp_id}"),
        )
    await db.commit()

    neurons = _load(FIXTURES / "eval_neurons.yaml")
    id_map: dict[str, int] = {}
    for n in neurons:
        # Create a synthetic source event (needed for source_event_ids
        # FK + the event-log-only-truth rule).
        payload = {"text": n["content"]}
        ch = compute_content_hash(payload)
        sig = sign(persona.private_key, canonical_signing_message(persona.id, ch))
        event = await append_event(
            db,
            persona_id=persona.id,
            counterparty_id=n.get("counterparty_id"),
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=sig,
            public_key_b64=persona.public_key_b64,
        )

        kind = n.get("kind") or (
            "counterparty_fact" if n.get("counterparty_id") is not None else "domain_fact"
        )
        cursor = await db.execute(
            """
            INSERT INTO neurons
                (persona_id, counterparty_id, kind, content, content_hash,
                 source_event_ids, source_count, distinct_source_count,
                 tier, embedder_rev)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
            """,
            (
                persona.id,
                n.get("counterparty_id"),
                kind,
                n["content"],
                ch,
                json.dumps([event.id]),
                n.get("tier", "working"),
                "paraphrase-multilingual-minilm-l12-v2-1",
            ),
        )
        await db.commit()
        neuron_id = cursor.lastrowid
        assert neuron_id is not None
        id_map[n["id"]] = neuron_id

        vec = embed_fn(n["content"])
        try:
            await db.execute(
                "INSERT INTO neurons_vec (neuron_id, embedding) VALUES (?, ?)",
                (neuron_id, json.dumps(list(vec))),
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — sqlite-vec may not be available in test env
            pass

    return id_map


def _mrr_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Reciprocal rank of first relevant hit in top-k. 0 if none."""
    for rank, neuron_id in enumerate(ranked_ids[:k], start=1):
        if neuron_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def _precision_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Fraction of top-k that are relevant. 0 if top-k empty."""
    top = ranked_ids[:k]
    if not top:
        return 0.0
    hits = sum(1 for nid in top if nid in relevant_ids)
    return hits / len(top)


def _hit_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> int:
    """1 if any relevant neuron is in top-k, else 0. Binary metric —
    unlike P@K it isn't bounded by the number of relevant items per query,
    so it's more interpretable on 1-relevant-per-query corpora."""
    return int(any(nid in relevant_ids for nid in ranked_ids[:k]))


def _ndcg_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Normalized Discounted Cumulative Gain @ k with binary relevance.

    DCG = Σ rel_i / log2(i + 1) over top-k positions (1-indexed).
    iDCG = best-case DCG (all relevant ranked first).
    NDCG = DCG / iDCG. 0 if no relevant items exist.
    """
    import math

    if not relevant_ids:
        return 0.0
    dcg = 0.0
    for rank, nid in enumerate(ranked_ids[:k], start=1):
        if nid in relevant_ids:
            dcg += 1.0 / math.log2(rank + 1)
    # Ideal DCG: all R relevant items ranked in the top R positions
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


async def test_retrieval_baseline_mrr_and_precision(db) -> None:
    """Compute MRR@10 and P@5 on the frozen seed corpus."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    def embed_fn(text: str) -> list[float]:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    persona = await make_test_persona(db)
    id_map = await _seed_corpus(db, persona, embed_fn)

    queries = _load(FIXTURES / "eval_queries.yaml")
    labels = _load(FIXTURES / "eval_relevance.yaml")
    labels_by_id = {lbl["query_id"]: lbl for lbl in labels}

    per_query: list[dict[str, Any]] = []
    adversarial: list[dict[str, Any]] = []
    zero_recall: list[str] = []

    for q in queries:
        label = labels_by_id.get(q["id"])
        if not label:
            pytest.fail(f"Missing relevance labels for {q['id']}")
        relevant_fixture_ids = set(label["relevant_ids"])
        relevant_db_ids = {id_map[fid] for fid in relevant_fixture_ids if fid in id_map}

        query_embedding = list(embed_fn(q["query"]))
        results = await recall(
            db,
            persona_id=persona.id,
            query=q["query"],
            lens=q["lens"],
            top_k=TOP_K,
            query_embedding=query_embedding,
            embedder_rev="paraphrase-multilingual-minilm-l12-v2-1",
        )
        ranked_ids = [r.neuron.id for r in results]
        is_adversarial = bool(q.get("adversarial"))

        if is_adversarial:
            # Adversarial: relevance is the empty set by construction.
            # Precision-on-adversarial = fraction of top-k positions that
            # ARE NOT in the fixture corpus OR are correctly empty. A
            # strict retrieval system would return empty, but BM25/vector
            # always return SOMETHING — the question is whether it's all
            # low-confidence filler (i.e. doesn't match any real cluster).
            adversarial.append({
                "query_id": q["id"],
                "query": q["query"],
                "n_retrieved": len(ranked_ids),
                "top_content": [
                    next((n for n in _load(FIXTURES / "eval_neurons.yaml")
                          if id_map.get(n["id"]) == nid), {}).get("content", "")[:50]
                    for nid in ranked_ids[:3]
                ],
            })
            continue

        if not relevant_db_ids:
            continue

        mrr = _mrr_at_k(ranked_ids, relevant_db_ids, k=10)
        p_at_5 = _precision_at_k(ranked_ids, relevant_db_ids, k=5)
        hit_at_5 = _hit_at_k(ranked_ids, relevant_db_ids, k=5)
        hit_at_10 = _hit_at_k(ranked_ids, relevant_db_ids, k=10)
        ndcg_at_10 = _ndcg_at_k(ranked_ids, relevant_db_ids, k=10)

        per_query.append({
            "query_id": q["id"],
            "query": q["query"],
            "lens": q["lens"],
            "n_relevant": len(relevant_db_ids),
            "n_retrieved": len(ranked_ids),
            "mrr_at_10": mrr,
            "p_at_5": p_at_5,
            "hit_at_5": hit_at_5,
            "hit_at_10": hit_at_10,
            "ndcg_at_10": ndcg_at_10,
            "first_hit_rank": next(
                (i + 1 for i, nid in enumerate(ranked_ids) if nid in relevant_db_ids),
                None,
            ),
        })
        if mrr == 0.0:
            zero_recall.append(q["id"])

    # Aggregate over non-adversarial
    n = len(per_query)
    avg_mrr = sum(r["mrr_at_10"] for r in per_query) / n if n else 0.0
    avg_p5 = sum(r["p_at_5"] for r in per_query) / n if n else 0.0
    avg_hit5 = sum(r["hit_at_5"] for r in per_query) / n if n else 0.0
    avg_hit10 = sum(r["hit_at_10"] for r in per_query) / n if n else 0.0
    avg_ndcg = sum(r["ndcg_at_10"] for r in per_query) / n if n else 0.0

    print(f"\n{'=' * 72}")
    print("RETRIEVAL BASELINE — P1 #4 (frozen 30-neuron / 20+5-query fixture)")
    print(f"{'=' * 72}")
    print(f"Stack: BM25Plus + vector (MiniLM-L12) + graph, RRF fusion")
    print(f"Corpus: 30 neurons across 6 clusters")
    print(f"Real queries: {n} (with non-empty relevance labels)")
    print(f"Adversarial: {len(adversarial)} (should return no corpus hits)")
    print(f"{'-' * 72}")
    print(f"MRR@10:       {avg_mrr:.3f}")
    print(f"NDCG@10:      {avg_ndcg:.3f}   ← proper ranked-relevance metric")
    print(f"Hit@5:        {avg_hit5:.3f}   ← any relevant in top 5")
    print(f"Hit@10:       {avg_hit10:.3f}   ← any relevant in top 10")
    print(f"P@5:          {avg_p5:.3f}   ← ceiling-bounded, see fixture")
    print(f"Zero-recall:  {len(zero_recall)}/{n} queries ({100 * len(zero_recall) / n if n else 0:.0f}%)")
    print(f"{'=' * 72}")
    print(f"{'query_id':<8} {'rank':>4} {'mrr':>5} {'hit@5':>5} {'ndcg':>5}   query")
    print(f"{'-' * 72}")
    for r in per_query:
        rank = r["first_hit_rank"]
        rank_str = str(rank) if rank else "-"
        print(
            f"{r['query_id']:<8} {rank_str:>4} {r['mrr_at_10']:>5.2f} "
            f"{r['hit_at_5']:>5} {r['ndcg_at_10']:>5.2f}   {r['query']}"
        )
    print(f"{'=' * 72}")
    print("ADVERSARIAL (corpus has no coverage — top-3 returned):")
    print(f"{'-' * 72}")
    for a in adversarial:
        print(f"{a['query_id']:<10} {a['query']}")
        for c in a["top_content"]:
            print(f"           ↳ {c}")
    print(f"{'=' * 72}")
    if zero_recall:
        print(f"Zero-recall queries: {zero_recall}")
    print(f"{'=' * 72}")

    assert avg_mrr >= 0.3, f"MRR@10 {avg_mrr:.3f} below 0.3 floor"
    assert avg_ndcg >= 0.3, f"NDCG@10 {avg_ndcg:.3f} below 0.3 floor"
    assert avg_hit10 >= 0.5, f"Hit@10 {avg_hit10:.3f} below 0.5 floor"
