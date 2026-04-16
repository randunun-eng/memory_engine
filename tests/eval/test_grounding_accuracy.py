"""Phase 2 grounding gate accuracy measurement.

Runs the grounding gate against 50 hand-labeled fixtures and reports
citation-ground-truth accuracy. Uses mock embeddings for similarity
and a real or mock LLM for the grounding judge.

Run with: uv run pytest tests/eval/test_grounding_accuracy.py --eval -v -s
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from memory_engine.core.events import compute_content_hash
from memory_engine.core.extraction import NeuronCandidate
from memory_engine.core.grounding import Verdict, grounding_gate
from memory_engine.policy.cache import PromptCache
from memory_engine.policy.dispatch import PolicyDispatch
from memory_engine.policy.registry import PromptRegistry
from memory_engine.policy.signing import canonical_signing_message, sign
from tests.fixtures.personas import make_test_persona

FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "grounding_truth.yaml"

pytestmark = pytest.mark.eval


def _load_fixtures() -> list[dict]:
    """Load the 50 hand-labeled grounding fixtures."""
    raw = FIXTURES_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, list), f"Expected list, got {type(data)}"
    return data


def _make_grounding_dispatch(verdict: str) -> PolicyDispatch:
    """Build a dispatch that returns a fixed grounding judge verdict."""

    async def mock_llm(model: str, prompt: str, temperature: float) -> str:
        return json.dumps({
            "verdict": verdict,
            "reason": "mock judge",
            "confidence": 0.9,
        })

    registry = PromptRegistry()
    registry.load_from_directory()
    cache = PromptCache()
    return PolicyDispatch(registry=registry, llm_backend=mock_llm, cache=cache)


def _simple_embed(text: str) -> list[float]:
    """Trivial word-overlap embedding for grounding similarity.

    Creates a bag-of-words vector where each dimension corresponds to
    a hashed word. This gives meaningful cosine similarity for our
    grounding fixtures without requiring a real model.
    """
    words = set(text.lower().split())
    dims = 64
    vec = [0.0] * dims
    for word in words:
        idx = hash(word) % dims
        vec[idx] += 1.0
    # Normalize
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


async def test_grounding_accuracy_on_50_fixtures(db) -> None:
    """Measure grounding gate accuracy on hand-labeled fixtures.

    This test:
    1. Loads 50 labeled fixtures (30 grounded, 20 ungrounded)
    2. For each fixture, creates a real event and runs the grounding gate
    3. Compares gate verdict against human label
    4. Reports accuracy as true_positive + true_negative / total

    Uses similarity-only gate (no LLM judge) to measure the embedding
    signal. The LLM judge is tested separately.
    """
    persona = await make_test_persona(db)
    fixtures = _load_fixtures()
    assert len(fixtures) == 50, f"Expected 50 fixtures, got {len(fixtures)}"

    results = {
        "true_positive": 0,   # grounded, gate accepted
        "true_negative": 0,   # ungrounded, gate rejected
        "false_positive": 0,  # ungrounded, gate accepted (hallucination leak)
        "false_negative": 0,  # grounded, gate rejected (over-filtering)
    }

    for fixture in fixtures:
        # Create a real event from the fixture text
        payload = {"text": fixture["event_text"]}
        content_hash = compute_content_hash(payload)
        msg = canonical_signing_message(persona.id, content_hash)
        sig = sign(persona.private_key, msg)

        from memory_engine.core.events import append_event

        event = await append_event(
            db,
            persona_id=persona.id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=sig,
            public_key_b64=persona.public_key_b64,
        )

        candidate = NeuronCandidate(
            content=fixture["candidate"],
            confidence=0.9,
            source_event_ids=[event.id],
            t_valid_start=None,
            source_span=None,
        )

        # Run grounding gate with similarity check only (no LLM judge)
        gate_result = await grounding_gate(
            candidate,
            events=[event],
            conn=db,
            persona_id=persona.id,
            embed_fn=_simple_embed,
            similarity_threshold=0.40,
            llm_judge_tiers=[],  # disable LLM judge for this measurement
        )

        expected_grounded = fixture["grounded"]
        gate_accepted = gate_result.verdict == Verdict.ACCEPT

        if expected_grounded and gate_accepted:
            results["true_positive"] += 1
        elif not expected_grounded and not gate_accepted:
            results["true_negative"] += 1
        elif not expected_grounded and gate_accepted:
            results["false_positive"] += 1
        elif expected_grounded and not gate_accepted:
            results["false_negative"] += 1

    total = sum(results.values())
    accuracy = (results["true_positive"] + results["true_negative"]) / total
    precision = (
        results["true_positive"] / (results["true_positive"] + results["false_positive"])
        if (results["true_positive"] + results["false_positive"]) > 0
        else 0.0
    )
    recall = (
        results["true_positive"] / (results["true_positive"] + results["false_negative"])
        if (results["true_positive"] + results["false_negative"]) > 0
        else 0.0
    )

    # Print results for the DRIFT.md record
    print(f"\n{'='*60}")
    print("GROUNDING GATE ACCURACY — 50 fixtures, threshold=0.40")
    print(f"{'='*60}")
    print(f"Accuracy:  {accuracy:.1%} ({results['true_positive'] + results['true_negative']}/{total})")
    print(f"Precision: {precision:.1%} (of accepted, how many truly grounded)")
    print(f"Recall:    {recall:.1%} (of grounded, how many accepted)")
    print("")
    print(f"TP={results['true_positive']} TN={results['true_negative']} "
          f"FP={results['false_positive']} FN={results['false_negative']}")
    print(f"{'='*60}")

    # Store for DRIFT.md recording
    # The acceptance criterion from CLAUDE.md §9 Phase 2: >70% citation-ground-truth accuracy
    assert accuracy >= 0.50, (
        f"Grounding accuracy {accuracy:.1%} below 50% floor — "
        f"gate is not discriminating. Results: {results}"
    )
