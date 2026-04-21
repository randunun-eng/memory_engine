"""Phase 0 invariant tests.

Governance rules enforced at this phase:
- Rule 1: events immutable (DB-level triggers)
- Rule 14: content hash determinism (foundation for neurons.source_event_ids)

These are defence-in-depth tests. Business-logic tests live in
tests/integration/test_phase0.py; these specifically check that invariants
cannot be violated even by direct SQL.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

# ---- Rule 1: events immutable ------------------------------------------


async def test_event_update_is_forbidden(db, seed_persona) -> None:
    """Rule 1: events are immutable at the DB layer.

    A direct UPDATE on a regular event must fail via the trigger, not rely
    on Python discipline. This catches any case where application code
    bypasses the API and writes SQL directly.
    """
    import aiosqlite

    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.policy.signing import canonical_signing_message, sign

    payload = {"text": "hello"}
    ch = compute_content_hash(payload)
    sig = sign(
        seed_persona.private_key,
        canonical_signing_message(seed_persona.id, ch),
    )
    event = await append_event(
        db,
        persona_id=seed_persona.id,
        counterparty_id=None,
        event_type="message_in",
        scope="private",
        payload=payload,
        signature=sig,
        public_key_b64=seed_persona.public_key_b64,
        idempotency_key="rule1-update",
    )

    # Direct UPDATE should raise via SQLite trigger
    with pytest.raises(aiosqlite.OperationalError) as exc:
        await db.execute(
            "UPDATE events SET payload = ? WHERE id = ?",
            ('{"tampered": true}', event.id),
        )
        await db.commit()

    # The trigger should mention immutability or rule 1
    assert "immutable" in str(exc.value).lower() or "rule 1" in str(exc.value).lower()


async def test_event_delete_is_forbidden(db, seed_persona) -> None:
    """Rule 1: deletion is as forbidden as update."""
    import aiosqlite

    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.policy.signing import canonical_signing_message, sign

    payload = {"text": "goodbye"}
    ch = compute_content_hash(payload)
    sig = sign(
        seed_persona.private_key,
        canonical_signing_message(seed_persona.id, ch),
    )
    event = await append_event(
        db,
        persona_id=seed_persona.id,
        counterparty_id=None,
        event_type="message_in",
        scope="private",
        payload=payload,
        signature=sig,
        public_key_b64=seed_persona.public_key_b64,
        idempotency_key="rule1-delete",
    )

    with pytest.raises(aiosqlite.OperationalError) as exc:
        await db.execute("DELETE FROM events WHERE id = ?", (event.id,))
        await db.commit()

    assert "immutable" in str(exc.value).lower() or "rule 1" in str(exc.value).lower()


# ---- Rule 14: content hash determinism (foundation) --------------------


@given(
    content=st.dictionaries(
        keys=st.text(min_size=1, max_size=50),
        values=st.one_of(
            st.text(max_size=200),
            st.integers(),
            st.booleans(),
            st.none(),
        ),
        min_size=1,
        max_size=10,
    )
)
def test_content_hash_is_deterministic(content: dict) -> None:
    """Hash of the same content is stable across calls.

    Property-based: any plausible payload hashes identically twice in a row.
    This is the foundation for idempotency, citations, and event integrity.
    """
    from memory_engine.core.events import compute_content_hash

    h1 = compute_content_hash(content)
    h2 = compute_content_hash(content)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


@given(
    content_a=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        min_size=1,
        max_size=5,
    ),
    content_b=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.text(max_size=50),
        min_size=1,
        max_size=5,
    ),
)
def test_different_content_has_different_hash(content_a: dict, content_b: dict) -> None:
    """Different content produces different hashes (with negligible collision chance).

    Hypothesis may generate identical dicts occasionally; filter them out.
    """
    from memory_engine.core.events import compute_content_hash

    if content_a == content_b:
        return  # can't assert inequality on equal dicts

    h_a = compute_content_hash(content_a)
    h_b = compute_content_hash(content_b)
    assert h_a != h_b
