"""Phase 0 integration tests.

These tests define the Phase 0 acceptance criterion. When all tests in this
file and `tests/invariants/test_phase0.py` pass, Phase 0 is complete.

Claude Code's Phase 0 task is to make these tests pass by implementing:
- src/memory_engine/db/connection.py
- src/memory_engine/db/migrations.py
- src/memory_engine/policy/signing.py
- src/memory_engine/core/events.py
- migrations/001_initial.sql (already provided)

Do not modify these tests to make them pass. If a test seems wrong, it's
because the implementation should match what the test asserts, not the
other way around.
"""

from __future__ import annotations

import pytest

# pytest-asyncio mode = "auto" — no need for @pytest.mark.asyncio


# ---- Schema ------------------------------------------------------------


async def test_schema_applies_clean(db) -> None:
    """All Phase 0 tables exist after migrations run."""
    from memory_engine.db.migrations import migration_status

    rows = await migration_status(db)
    names = [r["name"] for r in rows]
    assert "001_initial" in names, f"001_initial missing from {names}"

    # Spot-check the key tables and virtual table
    for table in ["personas", "counterparties", "events", "neurons"]:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = await cursor.fetchone()
        assert row is not None, f"Table {table} missing"

    # Virtual table shows up in sqlite_master with type='table' too
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE name='neurons_vec'")
    assert await cursor.fetchone() is not None, "neurons_vec virtual table missing"


async def test_schema_migrations_tracks_applied(db) -> None:
    """schema_migrations records version, name, and checksum."""
    cursor = await db.execute(
        "SELECT version, name, checksum FROM schema_migrations WHERE name='001_initial'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["version"] == 1
    assert row["name"] == "001_initial"
    assert len(row["checksum"]) == 64  # SHA-256 hex


# ---- Event round-trip --------------------------------------------------


async def test_event_round_trip(db, seed_persona) -> None:
    """Append one event, retrieve by id, verify hash is stable."""
    from memory_engine.core.events import append_event, compute_content_hash, get_event
    from memory_engine.policy.signing import canonical_signing_message, sign

    payload = {"text": "hello world", "channel": "test"}
    content_hash = compute_content_hash(payload)
    message = canonical_signing_message(seed_persona.id, content_hash)
    signature = sign(seed_persona.private_key, message)

    event = await append_event(
        db,
        persona_id=seed_persona.id,
        counterparty_id=None,
        event_type="message_in",
        scope="private",
        payload=payload,
        signature=signature,
        public_key_b64=seed_persona.public_key_b64,
        idempotency_key="test-1",
    )

    assert event.content_hash == content_hash
    assert event.payload == payload
    assert event.scope == "private"
    assert event.type == "message_in"

    retrieved = await get_event(db, event.id)
    assert retrieved is not None
    assert retrieved.content_hash == event.content_hash
    assert retrieved.payload == payload
    assert retrieved.id == event.id


async def test_get_event_missing_returns_none(db) -> None:
    """Fetching a nonexistent event id returns None, not an error."""
    from memory_engine.core.events import get_event

    result = await get_event(db, 99999)
    assert result is None


# ---- Idempotency -------------------------------------------------------


async def test_idempotency_key_rejects_duplicate(db, seed_persona) -> None:
    """Second append with the same idempotency_key raises IdempotencyConflict."""
    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.exceptions import IdempotencyConflict
    from memory_engine.policy.signing import canonical_signing_message, sign

    payload = {"text": "hello"}
    content_hash = compute_content_hash(payload)
    signature = sign(
        seed_persona.private_key,
        canonical_signing_message(seed_persona.id, content_hash),
    )

    await append_event(
        db,
        persona_id=seed_persona.id,
        counterparty_id=None,
        event_type="message_in",
        scope="private",
        payload=payload,
        signature=signature,
        public_key_b64=seed_persona.public_key_b64,
        idempotency_key="dupe-key",
    )

    with pytest.raises(IdempotencyConflict):
        await append_event(
            db,
            persona_id=seed_persona.id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=signature,
            public_key_b64=seed_persona.public_key_b64,
            idempotency_key="dupe-key",
        )


async def test_idempotency_none_allows_multiple(db, seed_persona) -> None:
    """Events with idempotency_key=None can be appended multiple times.

    SQLite's UNIQUE constraint treats NULL as distinct from NULL, so multiple
    NULL keys coexist. Tests rely on this for events without a natural key.
    """
    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.policy.signing import canonical_signing_message, sign

    for i in range(3):
        payload = {"text": f"msg {i}"}
        content_hash = compute_content_hash(payload)
        sig = sign(
            seed_persona.private_key,
            canonical_signing_message(seed_persona.id, content_hash),
        )
        await append_event(
            db,
            persona_id=seed_persona.id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=sig,
            public_key_b64=seed_persona.public_key_b64,
            idempotency_key=None,
        )

    cursor = await db.execute("SELECT count(*) AS c FROM events")
    row = await cursor.fetchone()
    assert row["c"] == 3


# ---- Signature verification --------------------------------------------


async def test_signature_verification_rejects_bad(db, seed_persona) -> None:
    """Tampered signature is rejected BEFORE any DB write."""
    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.exceptions import SignatureInvalid
    from memory_engine.policy.signing import canonical_signing_message, sign

    payload = {"text": "hi"}
    content_hash = compute_content_hash(payload)
    good_sig = sign(
        seed_persona.private_key,
        canonical_signing_message(seed_persona.id, content_hash),
    )
    # Corrupt the last 4 characters of the base64 signature
    bad_sig = good_sig[:-4] + "AAAA"

    with pytest.raises(SignatureInvalid):
        await append_event(
            db,
            persona_id=seed_persona.id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=bad_sig,
            public_key_b64=seed_persona.public_key_b64,
            idempotency_key="bad-sig",
        )

    # Confirm no event was written
    cursor = await db.execute("SELECT count(*) AS c FROM events")
    row = await cursor.fetchone()
    assert row["c"] == 0


async def test_signature_verification_rejects_wrong_key(db, seed_persona) -> None:
    """Signature verified against a different public key is rejected."""
    import base64

    from memory_engine.core.events import append_event, compute_content_hash
    from memory_engine.exceptions import SignatureInvalid
    from memory_engine.policy.signing import canonical_signing_message, generate_keypair, sign

    payload = {"text": "hi"}
    content_hash = compute_content_hash(payload)
    signature = sign(
        seed_persona.private_key,
        canonical_signing_message(seed_persona.id, content_hash),
    )

    # Use a different public key
    _, other_pub = generate_keypair()
    other_pub_b64 = base64.b64encode(other_pub).decode("ascii")

    with pytest.raises(SignatureInvalid):
        await append_event(
            db,
            persona_id=seed_persona.id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=signature,
            public_key_b64=other_pub_b64,
            idempotency_key="wrong-key",
        )


# ---- Persona / counterparty constraints --------------------------------


async def test_persona_slug_unique(db) -> None:
    """Two personas cannot share a slug."""
    import aiosqlite

    from tests.fixtures.personas import make_test_persona

    await make_test_persona(db, slug="shared_slug")
    with pytest.raises(aiosqlite.IntegrityError):
        await make_test_persona(db, slug="shared_slug")


async def test_counterparty_unique_per_persona(db, seed_persona) -> None:
    """Two counterparties with the same external_ref under the same persona collide."""
    import aiosqlite

    await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref, display_name) VALUES (?, ?, ?)",
        (seed_persona.id, "whatsapp:+1", "First"),
    )
    await db.commit()

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO counterparties (persona_id, external_ref) VALUES (?, ?)",
            (seed_persona.id, "whatsapp:+1"),
        )
        await db.commit()


# ---- Scope CHECK constraint --------------------------------------------


async def test_scope_invalid_value_rejected(db, seed_persona) -> None:
    """Inserting an event with scope outside the enum fails the CHECK."""
    import aiosqlite

    from memory_engine.core.events import compute_content_hash
    from memory_engine.policy.signing import canonical_signing_message, sign

    payload = {"text": "hi"}
    content_hash = compute_content_hash(payload)
    sig = sign(
        seed_persona.private_key,
        canonical_signing_message(seed_persona.id, content_hash),
    )

    # Bypassing append_event to hit the DB-level check directly
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO events
                (persona_id, type, scope, content_hash, payload, signature)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                seed_persona.id,
                "message_in",
                "secret",  # not in enum
                content_hash,
                '{"text":"hi"}',
                sig,
            ),
        )
        await db.commit()
