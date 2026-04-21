"""WhatsApp ingest pipeline.

Handles the full path from a WhatsApp message envelope to an immutable event:
  1. Resolve MCP token → persona binding
  2. Verify Ed25519 signature
  3. Canonicalize external_ref (phone or group JID)
  4. Lookup or create counterparty
  5. Check tombstones → reject on match
  6. Compute content hash + check idempotency
  7. Append event to the log

The MCP is responsible for:
  - Normalizing the raw WhatsApp message into a canonical envelope
  - Signing the envelope with its Ed25519 private key
  - Sending the WhatsApp message_id as the idempotency key

This module handles text messages and image references. Other message
types (reactions, read receipts, edits, polls, etc.) are out of scope
for Phase 5 — the adapter ships text in/out with image refs stored but
not processed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.adapters.whatsapp.canonicalize import (
    canonicalize_group_jid,
    canonicalize_phone,
)
from memory_engine.adapters.whatsapp.groups import (
    lookup_or_create_group_counterparty,
    prepare_sender_hint,
)
from memory_engine.adapters.whatsapp.mcp import resolve_token
from memory_engine.core.events import Event, append_event, compute_content_hash
from memory_engine.exceptions import (
    IdempotencyConflict,
    SignatureInvalid,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WhatsAppEnvelope:
    """Canonical envelope from a WhatsApp MCP.

    The MCP normalizes the raw WhatsApp message into this form before
    signing and posting to /v1/ingest.
    """

    external_ref: str  # phone number or group JID (raw, will be canonicalized)
    content: str  # text content of the message
    wa_message_id: str  # WhatsApp message ID (used as idempotency key)
    timestamp: str | None = (
        None  # MCP-reported timestamp (informational; server time is authoritative)
    )
    sender_hint: str | None = None  # for group messages: individual sender's phone
    display_name_hint: str | None = None  # contact or group name from WhatsApp
    forwarded_from: str | None = None  # original sender if forwarded
    image_ref: str | None = None  # reference to stored image (not the bytes)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Result of processing a WhatsApp ingest request."""

    event: Event
    counterparty_id: int
    mcp_source_id: int
    is_new_counterparty: bool


async def ingest_whatsapp_message(
    conn: aiosqlite.Connection,
    *,
    bearer_token: str,
    envelope: WhatsAppEnvelope,
    signature: str,
) -> IngestResult:
    """Process a WhatsApp message through the full ingest pipeline.

    This is the main entry point for the WhatsApp adapter. It handles
    authentication, canonicalization, counterparty management, tombstone
    checking, and event creation.

    Args:
        conn: Database connection.
        bearer_token: The MCP's bearer token for authentication.
        envelope: The canonical message envelope from the MCP.
        signature: Ed25519 signature of the canonical event body.

    Returns:
        IngestResult with the created event and metadata.

    Raises:
        SignatureInvalid: Token invalid or signature verification failed.
        ConfigError: Invalid external_ref format.
        IdempotencyConflict: Duplicate message (already ingested).
    """
    # Step 1: Resolve MCP token → persona
    mcp = await resolve_token(conn, bearer_token)
    if mcp is None:
        raise SignatureInvalid("Invalid or revoked MCP bearer token")

    persona_id = mcp.persona_id

    # Step 2: Canonicalize external_ref
    is_group = _looks_like_group(envelope.external_ref)
    if is_group:
        canonical_ref = canonicalize_group_jid(envelope.external_ref)
    else:
        canonical_ref = canonicalize_phone(envelope.external_ref)

    # Step 3: Lookup or create counterparty
    is_new_cp = False
    if is_group:
        cp_id = await lookup_or_create_group_counterparty(
            conn,
            persona_id=persona_id,
            group_jid=envelope.external_ref,
            display_name_hint=envelope.display_name_hint,
        )
    else:
        cp_id, is_new_cp = await _lookup_or_create_contact(
            conn,
            persona_id=persona_id,
            canonical_ref=canonical_ref,
            display_name_hint=envelope.display_name_hint,
        )

    # Step 4: Check tombstones
    tombstoned = await _check_tombstones(conn, persona_id, canonical_ref, envelope)
    if tombstoned:
        raise IdempotencyConflict(f"Message blocked by tombstone for {canonical_ref}")

    # Step 5: Build event payload
    payload = _build_payload(envelope, canonical_ref, is_group)

    # Step 6: Build idempotency key
    idempotency_key = f"wa:{mcp.name}:{envelope.wa_message_id}"

    # Step 7: Prepare sender_hint for group messages
    hint = prepare_sender_hint(envelope.sender_hint) if is_group else None

    # Step 8: Append event (signature verified inside append_event)
    # mcp_source_id and sender_hint are set at INSERT time because the
    # events immutability trigger (rule 1) prevents post-INSERT UPDATE.
    event = await append_event(
        conn,
        persona_id=persona_id,
        counterparty_id=cp_id,
        event_type="message_in",
        scope="shared",  # WhatsApp messages are shared by default
        payload=payload,
        signature=signature,
        public_key_b64=mcp.public_key_ed25519,
        idempotency_key=idempotency_key,
        mcp_source_id=mcp.id,
        sender_hint=hint,
    )

    logger.info(
        "Ingested WhatsApp message: persona=%d, counterparty=%d, event=%d, group=%s",
        persona_id,
        cp_id,
        event.id,
        is_group,
    )

    return IngestResult(
        event=event,
        counterparty_id=cp_id,
        mcp_source_id=mcp.id,
        is_new_counterparty=is_new_cp,
    )


def _looks_like_group(external_ref: str) -> bool:
    """Heuristic: is this a group reference?"""
    ref = external_ref.strip().lower()
    return ref.startswith("whatsapp-group:") or ref.endswith("@g.us")


async def _lookup_or_create_contact(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    canonical_ref: str,
    display_name_hint: str | None,
) -> tuple[int, bool]:
    """Find or create a 1:1 counterparty. Returns (id, is_new)."""
    cursor = await conn.execute(
        "SELECT id FROM counterparties WHERE persona_id = ? AND external_ref = ?",
        (persona_id, canonical_ref),
    )
    row = await cursor.fetchone()
    if row is not None:
        return row["id"], False

    cursor = await conn.execute(
        """
        INSERT INTO counterparties (persona_id, external_ref, display_name)
        VALUES (?, ?, ?)
        """,
        (persona_id, canonical_ref, display_name_hint),
    )
    await conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid, True


async def _check_tombstones(
    conn: aiosqlite.Connection,
    persona_id: int,
    canonical_ref: str,
    envelope: WhatsAppEnvelope,
) -> bool:
    """Check if any tombstone blocks this message.

    Checks for counterparty-level and content-hash-level tombstones.
    """
    # Check counterparty tombstone
    cursor = await conn.execute(
        "SELECT 1 FROM tombstones WHERE persona_id = ? AND scope = ?",
        (persona_id, f"counterparty:{canonical_ref}"),
    )
    if await cursor.fetchone() is not None:
        return True

    # Check content hash tombstone
    payload = _build_payload(envelope, canonical_ref, _looks_like_group(envelope.external_ref))
    content_hash = compute_content_hash(payload)
    cursor = await conn.execute(
        "SELECT 1 FROM tombstones WHERE persona_id = ? AND scope = ?",
        (persona_id, f"content_hash:{content_hash}"),
    )
    if await cursor.fetchone() is not None:
        return True

    # Check idempotency key tombstone
    cursor = await conn.execute(
        "SELECT 1 FROM tombstones WHERE persona_id = ? AND scope = ?",
        (persona_id, f"idempotency:wa:{envelope.wa_message_id}"),
    )
    return await cursor.fetchone() is not None


def _build_payload(
    envelope: WhatsAppEnvelope,
    canonical_ref: str,
    is_group: bool,
) -> dict[str, Any]:
    """Build the event payload from the envelope."""
    payload: dict[str, Any] = {
        "content": envelope.content,
        "source": "whatsapp",
        "external_ref": canonical_ref,
        "wa_message_id": envelope.wa_message_id,
    }

    if envelope.timestamp is not None:
        payload["mcp_timestamp"] = envelope.timestamp

    if is_group and envelope.sender_hint is not None:
        payload["sender_hint"] = envelope.sender_hint

    if envelope.forwarded_from is not None:
        payload["forwarded_from"] = envelope.forwarded_from

    if envelope.image_ref is not None:
        payload["image_ref"] = envelope.image_ref

    return payload
