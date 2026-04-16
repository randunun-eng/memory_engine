"""The grounding gate.

Every candidate neuron must pass this gate before promotion to the cortex.
Three checks, in order:

1. Citation resolution: every source_event_id must resolve to an actual event.
2. Similarity check: candidate content must share meaningful overlap with cited events.
3. LLM judge (for semantic/procedural tiers): an LLM verifies grounding.

Rejected candidates go to quarantine_neurons, not silently dropped.
The healer surfaces them in Phase 3's daily digest.

Config: grounding.similarity_threshold (default 0.40) and
grounding.llm_judge_required_for_tiers (default ["semantic", "procedural"]).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.core.events import Event
    from memory_engine.core.extraction import NeuronCandidate
    from memory_engine.policy.dispatch import PolicyDispatch

logger = logging.getLogger(__name__)


class Verdict(Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class GroundingResult:
    """Result of the grounding gate evaluation."""

    verdict: Verdict
    reason: str | None = None
    detail: str | None = None


async def grounding_gate(
    candidate: NeuronCandidate,
    events: list[Event],
    conn: aiosqlite.Connection,
    persona_id: int,
    dispatch: PolicyDispatch | None = None,
    similarity_threshold: float = 0.40,
    llm_judge_tiers: list[str] | None = None,
    embed_fn: Any | None = None,
) -> GroundingResult:
    """Evaluate a candidate neuron through the grounding gate.

    Args:
        candidate: The neuron candidate to evaluate.
        events: The resolved source events (caller pre-fetches).
        conn: DB connection for citation checks.
        persona_id: The persona this candidate belongs to.
        dispatch: Policy dispatch for LLM judge calls. Required if
            candidate.target_tier is in llm_judge_tiers.
        similarity_threshold: Minimum cosine similarity between candidate
            and source events. Default from config.
        llm_judge_tiers: Tiers that require LLM judge verification.
        embed_fn: Callable(text) -> list[float]. Required for similarity check.
            If None, similarity check is skipped (Phase 2 test mode).

    Returns:
        GroundingResult with verdict and reason.
    """
    if llm_judge_tiers is None:
        llm_judge_tiers = ["semantic", "procedural"]

    # Step 1: Citation resolution — every source_event_id must exist
    for eid in candidate.source_event_ids:
        exists = await _event_exists(conn, eid, persona_id)
        if not exists:
            return GroundingResult(
                verdict=Verdict.REJECT,
                reason="citation_unresolved",
                detail=f"event_id={eid} not found for persona={persona_id}",
            )

    # Step 2: Similarity check — candidate content vs cited event text
    if embed_fn is not None and events:
        source_text = _concatenate_event_text(events)
        try:
            candidate_vec = embed_fn(candidate.content)
            source_vec = embed_fn(source_text)
            sim = _cosine_similarity(candidate_vec, source_vec)
        except Exception:
            logger.warning("Embedding failed during grounding", exc_info=True)
            sim = 0.0

        if sim < similarity_threshold:
            return GroundingResult(
                verdict=Verdict.REJECT,
                reason="low_similarity",
                detail=f"cosine_sim={sim:.3f} < threshold={similarity_threshold}",
            )

    # Step 3: LLM judge for high-confidence tier promotion
    if candidate.target_tier in llm_judge_tiers and dispatch is not None:
        source_text = _concatenate_event_text(events)
        try:
            judge_response = await dispatch.dispatch(
                "grounding_judge",
                persona_id=persona_id,
                params={
                    "candidate_content": candidate.content,
                    "source_events_text": source_text,
                },
            )
            judge_verdict = judge_response.get("verdict", "ungrounded")
            if judge_verdict == "ungrounded":
                return GroundingResult(
                    verdict=Verdict.REJECT,
                    reason="llm_judge_ungrounded",
                    detail=judge_response.get("reason", ""),
                )
        except Exception:
            logger.warning("LLM grounding judge failed", exc_info=True)
            # On judge failure, reject conservatively
            return GroundingResult(
                verdict=Verdict.REJECT,
                reason="llm_judge_error",
                detail="LLM judge call failed; rejecting conservatively",
            )

    return GroundingResult(verdict=Verdict.ACCEPT)


async def quarantine_candidate(
    conn: aiosqlite.Connection,
    candidate: NeuronCandidate,
    persona_id: int,
    reason: str,
) -> int:
    """Write a rejected candidate to quarantine_neurons.

    Returns the quarantine row id.
    """
    candidate_json = json.dumps({
        "content": candidate.content,
        "confidence": candidate.confidence,
        "kind": candidate.kind,
        "target_tier": candidate.target_tier,
        "t_valid_start": candidate.t_valid_start,
        "source_span": candidate.source_span,
    })
    source_ids_json = json.dumps(candidate.source_event_ids)

    cursor = await conn.execute(
        """
        INSERT INTO quarantine_neurons
            (persona_id, candidate_json, reason, source_event_ids)
        VALUES (?, ?, ?, ?)
        """,
        (persona_id, candidate_json, reason, source_ids_json),
    )
    await conn.commit()
    row_id = cursor.lastrowid
    assert row_id is not None
    return row_id


async def _event_exists(conn: aiosqlite.Connection, event_id: int, persona_id: int) -> bool:
    """Check if an event exists for the given persona."""
    cursor = await conn.execute(
        "SELECT 1 FROM events WHERE id = ? AND persona_id = ?",
        (event_id, persona_id),
    )
    return await cursor.fetchone() is not None


def _concatenate_event_text(events: list[Event]) -> str:
    """Build source text from events for similarity comparison."""
    parts = []
    for event in events:
        payload = event.payload
        text = payload.get("text", payload.get("body", payload.get("content", "")))
        if not text and isinstance(payload, dict):
            text = json.dumps(payload)
        parts.append(str(text))
    return " ".join(parts)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))
