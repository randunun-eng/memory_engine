"""Contradiction detection — same-entity-pair only.

When a new neuron candidate overlaps with an existing neuron on the same
entity key, we check whether they contradict, refine, or complement.

Contradictions trigger supersession: the newer neuron supersedes the older.
Refinements keep both. Complements keep both.

All LLM calls go through dispatch (policy plane invariant).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.policy.dispatch import PolicyDispatch

logger = logging.getLogger(__name__)

Relation = Literal["contradict", "refine", "complement"]


@dataclass(frozen=True, slots=True)
class ContradictionResult:
    """Result of contradiction check between two neurons."""

    relation: Relation
    reason: str
    newer: str | None  # "a", "b", or None
    confidence: float


async def check_contradiction(
    dispatch: PolicyDispatch,
    *,
    persona_id: int,
    neuron_a_content: str,
    neuron_b_content: str,
    entity_key: str,
) -> ContradictionResult:
    """Check if two claims about the same entity contradict.

    Args:
        dispatch: Policy dispatch for the LLM judge call.
        persona_id: For cache scoping and audit.
        neuron_a_content: Text of the existing neuron.
        neuron_b_content: Text of the new candidate.
        entity_key: The shared entity (e.g. "Alex:birthday", "MPPT:efficiency").

    Returns:
        ContradictionResult with the relation type and metadata.
    """
    response = await dispatch.dispatch(
        "judge_contradiction",
        persona_id=persona_id,
        params={
            "neuron_a": neuron_a_content,
            "neuron_b": neuron_b_content,
            "entity_key": entity_key,
        },
    )

    return _parse_contradiction_response(response)


async def find_overlapping_neurons(
    conn: aiosqlite.Connection,
    persona_id: int,
    candidate_content: str,
    kind: str,
    counterparty_id: int | None = None,
) -> list[dict[str, Any]]:
    """Find existing active neurons that might overlap with a candidate.

    Uses a simple keyword overlap heuristic for Phase 2. Phase 3+ can add
    embedding-based similarity for better recall.

    Returns list of dicts with id, content, kind, counterparty_id.
    """
    # Build query based on kind and counterparty
    if kind == "counterparty_fact" and counterparty_id is not None:
        cursor = await conn.execute(
            """
            SELECT id, content, kind, counterparty_id
            FROM neurons
            WHERE persona_id = ?
              AND kind = ?
              AND counterparty_id = ?
              AND superseded_at IS NULL
            """,
            (persona_id, kind, counterparty_id),
        )
    else:
        cursor = await conn.execute(
            """
            SELECT id, content, kind, counterparty_id
            FROM neurons
            WHERE persona_id = ?
              AND kind = ?
              AND superseded_at IS NULL
            """,
            (persona_id, kind),
        )

    rows = await cursor.fetchall()

    # Simple keyword overlap filter — avoids sending every neuron to the LLM
    candidate_words = set(candidate_content.lower().split())
    overlapping = []
    for row in rows:
        existing_words = set(row["content"].lower().split())
        overlap = candidate_words & existing_words
        # Require at least 2 non-stopword overlapping words
        meaningful = overlap - _STOPWORDS
        if len(meaningful) >= 2:
            overlapping.append({
                "id": row["id"],
                "content": row["content"],
                "kind": row["kind"],
                "counterparty_id": row["counterparty_id"],
            })

    return overlapping


async def supersede_neuron(
    conn: aiosqlite.Connection,
    old_neuron_id: int,
    new_neuron_id: int,
) -> None:
    """Mark an old neuron as superseded by a new one.

    Rule 8: every neuron mutation emits an event. The caller is responsible
    for emitting the supersession event; this function only updates the neuron.
    """
    await conn.execute(
        """
        UPDATE neurons
        SET superseded_at = datetime('now'),
            superseded_by = ?
        WHERE id = ?
        """,
        (new_neuron_id, old_neuron_id),
    )
    await conn.commit()


def _parse_contradiction_response(response: dict[str, Any]) -> ContradictionResult:
    """Parse the judge_contradiction LLM response."""
    relation = response.get("relation", "complement")
    if relation not in ("contradict", "refine", "complement"):
        logger.warning("Unknown contradiction relation: %s, defaulting to complement", relation)
        relation = "complement"

    newer = response.get("newer")
    if newer not in ("a", "b", None):
        newer = None

    return ContradictionResult(
        relation=relation,
        reason=response.get("reason", ""),
        newer=newer,
        confidence=float(response.get("confidence", 0.0)),
    )


# Minimal stopword set for overlap filtering
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "not", "no", "nor", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some", "such",
    "than", "too", "very", "just", "about", "that", "this", "these", "those",
    "it", "its", "he", "she", "they", "them", "his", "her", "their", "i",
    "me", "my", "we", "us", "our", "you", "your",
})
