"""Identity drift detection.

Monitors outbound candidates for contradictions against the persona's
identity document. Flags drift for human review — never auto-modifies
the identity document (rule 11).

Drift types:
- value_contradiction: candidate contradicts a self_fact
- nonneg_violation: candidate violates a non-negotiable
- forbidden_topic: candidate mentions a forbidden topic
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.identity.persona import IdentityDocument

logger = logging.getLogger(__name__)


async def flag_identity_drift(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    flag_type: str,
    candidate_text: str,
    rule_text: str | None = None,
) -> int:
    """Record an identity drift flag for human review.

    Args:
        conn: Database connection.
        persona_id: The persona whose identity is potentially violated.
        flag_type: One of 'value_contradiction', 'role_drift', 'tone_drift',
            'nonneg_violation', 'forbidden_topic'.
        candidate_text: The outbound text that triggered the flag.
        rule_text: The specific non-negotiable or self_fact violated.

    Returns:
        The id of the created flag row.
    """
    cursor = await conn.execute(
        """
        INSERT INTO identity_drift_flags
            (persona_id, flag_type, candidate_text, rule_text)
        VALUES (?, ?, ?, ?)
        """,
        (persona_id, flag_type, candidate_text, rule_text),
    )
    await conn.commit()
    flag_id = cursor.lastrowid
    assert flag_id is not None

    logger.warning(
        "Identity drift flagged for persona %d: %s — %s",
        persona_id,
        flag_type,
        (rule_text or "no rule specified")[:80],
    )

    return flag_id


def check_forbidden_topics(
    text: str,
    identity: IdentityDocument,
) -> str | None:
    """Check if text mentions any forbidden topic.

    Uses simple substring matching (case-insensitive). The LLM-based
    nonneg_judge handles more nuanced cases; this is the cheap first pass.

    Returns:
        The matched forbidden topic, or None if clean.
    """
    text_lower = text.lower()
    for topic in identity.forbidden_topics:
        # Match whole word boundaries loosely
        topic_lower = topic.lower().replace("_", " ")
        if topic_lower in text_lower:
            return topic
    return None


def check_self_fact_contradiction(
    text: str,
    identity: IdentityDocument,
) -> str | None:
    """Check if text contradicts a self_fact.

    This is a simple negation heuristic. The real contradiction detection
    lives in the LLM judge (core/contradiction.py); this catches obvious
    cases like "I am not based in Colombo" when self_facts says "I am
    based in Colombo, Sri Lanka."

    Returns:
        The contradicted self_fact text, or None if clean.
    """
    text_lower = text.lower()

    # Simple negation patterns
    negation_prefixes = ("i am not ", "i'm not ", "i do not ", "i don't ", "i never ")

    for fact in identity.self_facts:
        fact_lower = fact.text.lower()
        # Check if the candidate negates the fact
        for prefix in negation_prefixes:
            if prefix in text_lower:
                # Extract the claim after negation
                neg_start = text_lower.index(prefix) + len(prefix)
                neg_claim = text_lower[neg_start : neg_start + 50]
                # Check overlap with fact
                fact_words = set(fact_lower.split())
                claim_words = set(neg_claim.split())
                overlap = fact_words & claim_words - {"a", "the", "in", "is", "am", "i"}
                if len(overlap) >= 2:
                    return fact.text

    return None
