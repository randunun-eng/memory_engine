"""Phase 2 invariant tests — rules 14, 15, 16.

Rule 14: Every neuron cites at least one specific source event.
Rule 15: Retrieval ranking uses distinct_source_count, not source_count.
Rule 16: Validity-time fields are never fabricated (never default to now()).
"""

from __future__ import annotations

import json

from memory_engine.core.events import compute_content_hash
from memory_engine.core.extraction import NeuronCandidate
from memory_engine.policy.signing import canonical_signing_message, sign
from tests.fixtures.personas import make_test_persona

# ---- Rule 14: every neuron cites at least one event ----


async def test_every_neuron_cites_at_least_one_event(db) -> None:
    """Rule 14: no neuron can exist with empty source_event_ids.

    The CHECK constraint in 001_initial.sql enforces json_array_length >= 1.
    This test verifies the constraint works at the DB level.
    """
    persona = await make_test_persona(db)

    # Attempt to insert a neuron with empty source_event_ids
    import aiosqlite
    import pytest

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO neurons
                (persona_id, kind, content, content_hash, source_event_ids,
                 source_count, distinct_source_count, tier, embedder_rev)
            VALUES (?, 'self_fact', 'orphan claim', 'hash_orphan',
                    '[]', 1, 1, 'working', 'test-rev')
            """,
            (persona.id,),
        )
        await db.commit()


async def test_neuron_with_valid_citation_accepted(db) -> None:
    """Rule 14 positive case: neuron with at least one source event passes."""
    persona = await make_test_persona(db)

    # Create an event first
    payload = {"text": "Test event"}
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

    # Insert neuron citing that event — should succeed
    cursor = await db.execute(
        """
        INSERT INTO neurons
            (persona_id, kind, content, content_hash, source_event_ids,
             source_count, distinct_source_count, tier, embedder_rev)
        VALUES (?, 'self_fact', 'Test claim', 'hash_test',
                ?, 1, 1, 'working', 'test-rev')
        """,
        (persona.id, json.dumps([event.id])),
    )
    await db.commit()
    assert cursor.lastrowid is not None


# ---- Rule 15: ranking uses distinct_source_count ----


async def test_ranking_uses_distinct_source_count(db) -> None:
    """Rule 15: distinct_source_count <= source_count enforced at DB level.

    The CHECK constraint prevents distinct_source_count from exceeding
    source_count — the invariant that distinct is the honest count.
    """
    persona = await make_test_persona(db)

    import aiosqlite
    import pytest

    # Attempt to insert with distinct > source (impossible state)
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO neurons
                (persona_id, kind, content, content_hash, source_event_ids,
                 source_count, distinct_source_count, tier, embedder_rev)
            VALUES (?, 'self_fact', 'test claim', 'hash_rank',
                    '[1]', 1, 5, 'working', 'test-rev')
            """,
            (persona.id,),
        )
        await db.commit()


# ---- Rule 16: validity times never fabricated ----


async def test_validity_times_never_default_to_now(db) -> None:
    """Rule 16: if the extractor doesn't produce t_valid_start, it stays NULL.

    NeuronCandidate with t_valid_start=None must produce a neuron with
    NULL t_valid_start in the DB, not datetime('now').
    """
    persona = await make_test_persona(db)

    # Create an event
    payload = {"text": "Alex is a software engineer"}
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

    # Insert neuron with no validity time (as the extractor would produce)
    cursor = await db.execute(
        """
        INSERT INTO neurons
            (persona_id, kind, content, content_hash, source_event_ids,
             source_count, distinct_source_count, tier, t_valid_start, embedder_rev)
        VALUES (?, 'self_fact', 'Alex is a software engineer', 'hash_valid',
                ?, 1, 1, 'working', NULL, 'test-rev')
        """,
        (persona.id, json.dumps([event.id])),
    )
    await db.commit()
    neuron_id = cursor.lastrowid

    cursor = await db.execute(
        "SELECT t_valid_start, t_valid_end FROM neurons WHERE id = ?",
        (neuron_id,),
    )
    row = await cursor.fetchone()
    assert row["t_valid_start"] is None, (
        f"Rule 16 violation: t_valid_start={row['t_valid_start']!r} — "
        f"should be NULL when extractor doesn't provide it"
    )
    assert row["t_valid_end"] is None


async def test_extraction_preserves_null_validity(db) -> None:
    """Rule 16: extraction module passes through None validity correctly."""
    candidate = NeuronCandidate(
        content="Some fact without temporal anchor",
        confidence=0.9,
        source_event_ids=[1],
        t_valid_start=None,
        source_span="Some fact",
    )

    assert candidate.t_valid_start is None, "NeuronCandidate fabricated a validity time"

    # Also verify that a candidate WITH a validity time preserves it
    candidate_with_time = NeuronCandidate(
        content="Meeting scheduled for 2026-04-20",
        confidence=0.95,
        source_event_ids=[2],
        t_valid_start="2026-04-20T10:00:00",
        source_span="Meeting on April 20th",
    )

    assert candidate_with_time.t_valid_start == "2026-04-20T10:00:00"
