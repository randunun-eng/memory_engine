"""Phase 5 integration tests — WhatsApp adapter.

Tests verify the full WhatsApp adapter pipeline: MCP registration,
signature verification, phone canonicalization, group handling,
counterparty creation, tombstone enforcement, outbound preparation,
and the T3/T11 release gates.

Scope: text messages in and out, image references stored but not
processed. No reactions, read receipts, edits, polls, or channels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.adapters.whatsapp.canonicalize import (
    canonicalize_group_jid,
    canonicalize_phone,
    is_group_ref,
)
from memory_engine.adapters.whatsapp.ingest import (
    IngestResult,
    WhatsAppEnvelope,
    ingest_whatsapp_message,
)
from memory_engine.adapters.whatsapp.mcp import (
    register_mcp,
    resolve_token,
    revoke_mcp,
)
from memory_engine.adapters.whatsapp.outbound import prepare_outbound
from memory_engine.core.events import compute_content_hash
from memory_engine.exceptions import ConfigError, IdempotencyConflict, SignatureInvalid
from memory_engine.policy.signing import canonical_signing_message, sign
from tests.fixtures.personas import make_test_persona

# ---- Helpers ----


async def _register_mcp_for_persona(
    db: aiosqlite.Connection,
    persona_id: int,
    public_key_b64: str,
    *,
    name: str = "whatsapp-test",
) -> tuple:
    """Register an MCP and return (mcp_source, bearer_token)."""
    return await register_mcp(
        db,
        persona_id=persona_id,
        kind="whatsapp",
        name=name,
        public_key_b64=public_key_b64,
    )


def _sign_envelope(
    envelope: WhatsAppEnvelope,
    persona_id: int,
    private_key: bytes,
    *,
    canonical_ref: str | None = None,
    is_group: bool = False,
) -> str:
    """Sign an envelope's payload, returning the base64 signature."""
    from memory_engine.adapters.whatsapp.ingest import _build_payload

    if canonical_ref is None:
        if is_group or envelope.external_ref.endswith("@g.us"):
            canonical_ref = canonicalize_group_jid(envelope.external_ref)
        else:
            canonical_ref = canonicalize_phone(envelope.external_ref)

    payload = _build_payload(envelope, canonical_ref, is_group)
    content_hash = compute_content_hash(payload)
    message = canonical_signing_message(persona_id, content_hash)
    return sign(private_key, message)


async def _full_ingest(
    db: aiosqlite.Connection,
    persona,
    bearer_token: str,
    *,
    external_ref: str = "+94771234567",
    content: str = "Hello from WhatsApp",
    wa_message_id: str = "msg_001",
    sender_hint: str | None = None,
    display_name_hint: str | None = None,
    forwarded_from: str | None = None,
    image_ref: str | None = None,
) -> IngestResult:
    """Helper to ingest a single WhatsApp message."""
    is_group = external_ref.endswith("@g.us") or external_ref.startswith("whatsapp-group:")
    envelope = WhatsAppEnvelope(
        external_ref=external_ref,
        content=content,
        wa_message_id=wa_message_id,
        sender_hint=sender_hint,
        display_name_hint=display_name_hint,
        forwarded_from=forwarded_from,
        image_ref=image_ref,
    )
    sig = _sign_envelope(
        envelope, persona.id, persona.private_key, is_group=is_group,
    )
    return await ingest_whatsapp_message(
        db,
        bearer_token=bearer_token,
        envelope=envelope,
        signature=sig,
    )


# ---- MCP registration tests ----


async def test_mcp_register_and_resolve(db: aiosqlite.Connection) -> None:
    """MCP registration creates a source and the token resolves."""
    persona = await make_test_persona(db)
    mcp, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    assert mcp.persona_id == persona.id
    assert mcp.kind == "whatsapp"
    assert mcp.revoked_at is None

    resolved = await resolve_token(db, token)
    assert resolved is not None
    assert resolved.id == mcp.id


async def test_mcp_signature_verifies(db: aiosqlite.Connection) -> None:
    """Valid MCP signature allows ingest."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    result = await _full_ingest(db, persona, token, content="Test message")
    assert result.event.type == "message_in"
    assert result.event.persona_id == persona.id


async def test_mcp_signature_invalid_rejects(db: aiosqlite.Connection) -> None:
    """Invalid signature rejects the ingest."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    envelope = WhatsAppEnvelope(
        external_ref="+94771234567",
        content="Tampered message",
        wa_message_id="msg_bad",
    )

    with pytest.raises(SignatureInvalid):
        await ingest_whatsapp_message(
            db,
            bearer_token=token,
            envelope=envelope,
            signature="AAAA_invalid_signature_AAAA",
        )


async def test_mcp_revoked_token_rejects(db: aiosqlite.Connection) -> None:
    """Revoked MCP token rejects ingest."""
    persona = await make_test_persona(db)
    mcp, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    await revoke_mcp(db, mcp.id)

    envelope = WhatsAppEnvelope(
        external_ref="+94771234567",
        content="Should be rejected",
        wa_message_id="msg_revoked",
    )
    sig = _sign_envelope(envelope, persona.id, persona.private_key)

    with pytest.raises(SignatureInvalid, match="Invalid or revoked"):
        await ingest_whatsapp_message(
            db,
            bearer_token=token,
            envelope=envelope,
            signature=sig,
        )


async def test_mcp_duplicate_name_rejects(db: aiosqlite.Connection) -> None:
    """Duplicate MCP name for same persona raises ConfigError."""
    persona = await make_test_persona(db)
    await _register_mcp_for_persona(db, persona.id, persona.public_key_b64, name="wa-dup")

    with pytest.raises(ConfigError, match="already registered"):
        await _register_mcp_for_persona(db, persona.id, persona.public_key_b64, name="wa-dup")


# ---- Phone canonicalization tests ----


async def test_phone_canonicalization(db: aiosqlite.Connection) -> None:
    """Different phone formats canonicalize to the same external_ref."""
    variants = [
        "+94 77 123 4567",
        "+94-77-123-4567",
        "+94(77)1234567",
        "94771234567",
        "+94771234567",
        "whatsapp:+94771234567",
        "whatsapp:+94 77 123 4567",
    ]
    expected = "whatsapp:+94771234567"
    for v in variants:
        assert canonicalize_phone(v) == expected, f"Failed for {v!r}"


async def test_phone_canonicalization_invalid(db: aiosqlite.Connection) -> None:
    """Invalid phone number raises ConfigError."""
    with pytest.raises(ConfigError, match="Cannot canonicalize"):
        canonicalize_phone("abc")


async def test_phone_variants_create_one_counterparty(db: aiosqlite.Connection) -> None:
    """All variants of one phone number map to one counterparty, not multiple."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    # Ingest with different phone formats — should all map to same counterparty
    for i, phone in enumerate(["+94 77 123 4567", "+94-77-123-4567", "+94771234567"]):
        result = await _full_ingest(
            db, persona, token,
            external_ref=phone,
            content=f"Message {i}",
            wa_message_id=f"msg_variant_{i}",
        )
        assert result.counterparty_id == result.counterparty_id  # sanity

    # Verify only one counterparty was created
    cursor = await db.execute(
        "SELECT COUNT(*) FROM counterparties WHERE persona_id = ?",
        (persona.id,),
    )
    row = await cursor.fetchone()
    assert row[0] == 1


# ---- Group tests ----


async def test_group_becomes_counterparty(db: aiosqlite.Connection) -> None:
    """A WhatsApp group creates a single counterparty row."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    result = await _full_ingest(
        db, persona, token,
        external_ref="1234567890-1699999999@g.us",
        content="Group message",
        wa_message_id="grp_001",
        sender_hint="+94771234567",
        display_name_hint="Acme Team",
    )

    cursor = await db.execute(
        "SELECT external_ref, display_name FROM counterparties WHERE id = ?",
        (result.counterparty_id,),
    )
    row = await cursor.fetchone()
    assert row["external_ref"] == "whatsapp-group:1234567890-1699999999@g.us"
    assert row["display_name"] == "Acme Team"


async def test_sender_hint_stored_but_not_queried(db: aiosqlite.Connection) -> None:
    """sender_hint is stored on the event but doesn't create a sub-counterparty."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    result = await _full_ingest(
        db, persona, token,
        external_ref="1234567890-1699999999@g.us",
        content="Alice in group",
        wa_message_id="grp_alice",
        sender_hint="+94771111111",
    )

    # sender_hint should be stored on the event
    cursor = await db.execute(
        "SELECT sender_hint FROM events WHERE id = ?",
        (result.event.id,),
    )
    row = await cursor.fetchone()
    assert row["sender_hint"] == "whatsapp:+94771111111"

    # No counterparty should exist for the sender
    cursor = await db.execute(
        "SELECT COUNT(*) FROM counterparties WHERE external_ref = 'whatsapp:+94771111111'",
    )
    row = await cursor.fetchone()
    assert row[0] == 0


async def test_group_jid_canonicalization(db: aiosqlite.Connection) -> None:
    """Group JID canonicalization works correctly."""
    assert canonicalize_group_jid("1234@g.us") == "whatsapp-group:1234@g.us"
    assert canonicalize_group_jid("whatsapp-group:1234@g.us") == "whatsapp-group:1234@g.us"

    with pytest.raises(ConfigError, match="Cannot canonicalize"):
        canonicalize_group_jid("not-a-group-jid")


async def test_is_group_ref(db: aiosqlite.Connection) -> None:
    """is_group_ref correctly identifies group references."""
    assert is_group_ref("whatsapp-group:1234@g.us") is True
    assert is_group_ref("whatsapp:+94771234567") is False


# ---- Forwarded message tests ----


async def test_forwarded_message_attributes_to_forwarder(db: aiosqlite.Connection) -> None:
    """Forwarded messages attribute to the forwarder, not the original author."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    result = await _full_ingest(
        db, persona, token,
        external_ref="+94771111111",
        content="Forwarded content from someone else",
        wa_message_id="fwd_001",
        display_name_hint="Alice",
        forwarded_from="+94772222222",
    )

    # Event should be attributed to Alice's counterparty
    cursor = await db.execute(
        "SELECT external_ref FROM counterparties WHERE id = ?",
        (result.counterparty_id,),
    )
    row = await cursor.fetchone()
    assert row["external_ref"] == "whatsapp:+94771111111"

    # forwarded_from should be in the payload
    assert result.event.payload.get("forwarded_from") == "+94772222222"

    # No counterparty should exist for the original sender
    cursor = await db.execute(
        "SELECT COUNT(*) FROM counterparties WHERE external_ref = 'whatsapp:+94772222222'",
    )
    row = await cursor.fetchone()
    assert row[0] == 0


# ---- Image reference test ----


async def test_image_ref_stored_in_payload(db: aiosqlite.Connection) -> None:
    """Image reference is stored in the event payload (not processed)."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    result = await _full_ingest(
        db, persona, token,
        content="Check out this photo",
        wa_message_id="img_001",
        image_ref="vault://images/abc123.enc",
    )

    assert result.event.payload.get("image_ref") == "vault://images/abc123.enc"


# ---- Tombstone tests ----


async def test_tombstone_prevents_reingestion(db: aiosqlite.Connection) -> None:
    """A counterparty tombstone blocks ingestion from that counterparty."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    # Create a tombstone for a specific counterparty
    await db.execute(
        "INSERT INTO tombstones (persona_id, scope, reason) VALUES (?, ?, ?)",
        (persona.id, "counterparty:whatsapp:+94779999999", "blocked by operator"),
    )
    await db.commit()

    envelope = WhatsAppEnvelope(
        external_ref="+94779999999",
        content="Should be blocked",
        wa_message_id="tomb_001",
    )
    sig = _sign_envelope(envelope, persona.id, persona.private_key)

    with pytest.raises(IdempotencyConflict, match="tombstone"):
        await ingest_whatsapp_message(
            db,
            bearer_token=token,
            envelope=envelope,
            signature=sig,
        )


# ---- Idempotency tests ----


async def test_idempotency_rejects_duplicate(db: aiosqlite.Connection) -> None:
    """Same wa_message_id from same MCP is rejected as duplicate."""
    persona = await make_test_persona(db)
    _, token = await _register_mcp_for_persona(db, persona.id, persona.public_key_b64)

    await _full_ingest(db, persona, token, wa_message_id="dup_001")

    with pytest.raises(IdempotencyConflict):
        await _full_ingest(db, persona, token, wa_message_id="dup_001", content="duplicate")


# ---- Outbound tests ----


async def test_outbound_approved_creates_event(db: aiosqlite.Connection) -> None:
    """Approved outbound message creates a persona_output event."""
    persona = await make_test_persona(db)

    # Create a counterparty
    cursor = await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref, display_name) VALUES (?, ?, ?)",
        (persona.id, "whatsapp:+94771234567", "Alice"),
    )
    await db.commit()
    cp_id = cursor.lastrowid

    result = await prepare_outbound(
        db,
        persona_id=persona.id,
        counterparty_id=cp_id,
        reply_text="Thanks for reaching out!",
        private_key=persona.private_key,
        public_key_b64=persona.public_key_b64,
    )

    assert result.approval.verdict.value == "approved"
    assert result.event_id is not None

    # Verify the event was created
    cursor = await db.execute(
        "SELECT type, payload FROM events WHERE id = ?",
        (result.event_id,),
    )
    row = await cursor.fetchone()
    assert row["type"] == "message_out"


async def test_outbound_blocked_no_event(db: aiosqlite.Connection) -> None:
    """Blocked outbound message does not create an event."""
    persona = await make_test_persona(db)

    # Set up identity with non-negotiables
    from memory_engine.identity.persona import save_identity
    await save_identity(db, persona.id, """\
persona: test
version: 1
signed_by: test@example.org
signed_at: 2026-04-16T10:00:00Z
non_negotiables:
  - "I never disclose personal email or phone number."
""")

    cursor = await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref, display_name) VALUES (?, ?, ?)",
        (persona.id, "whatsapp:+94771234567", "Alice"),
    )
    await db.commit()
    cp_id = cursor.lastrowid

    result = await prepare_outbound(
        db,
        persona_id=persona.id,
        counterparty_id=cp_id,
        reply_text="The personal email is secret@private.com and phone number is +94771234567.",
        private_key=persona.private_key,
        public_key_b64=persona.public_key_b64,
    )

    assert result.approval.verdict.value == "blocked"
    assert result.event_id is None

    # No message_out event should exist
    cursor = await db.execute(
        "SELECT COUNT(*) FROM events WHERE persona_id = ? AND type = 'message_out'",
        (persona.id,),
    )
    row = await cursor.fetchone()
    assert row[0] == 0
