"""Event log append, retrieve, and hash.

The event log is the only source of truth (principle 1). Events are immutable
(rule 1). Every event carries a signature that must verify at ingress.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite

from memory_engine.exceptions import IdempotencyConflict
from memory_engine.policy.signing import canonical_signing_message, verify

logger = logging.getLogger(__name__)

Scope = Literal["private", "shared", "public"]
EventType = Literal[
    "message_in", "message_out", "retrieval_trace", "prompt_promoted", "operator_action"
]


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    persona_id: int
    counterparty_id: int | None
    type: str
    scope: Scope
    content_hash: str
    idempotency_key: str | None
    payload: dict[str, Any]
    signature: str
    recorded_at: datetime


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Canonical SHA-256 of a payload.

    Canonicalization: JSON with sorted keys, no whitespace, UTF-8 bytes.
    Same payload always produces the same hash.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def append_event(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    counterparty_id: int | None,
    event_type: str,
    scope: Scope,
    payload: dict[str, Any],
    signature: str,
    public_key_b64: str,
    idempotency_key: str | None = None,
) -> Event:
    """Append an event to the immutable log.

    Verifies signature before writing. Rejects duplicates by idempotency_key.
    Does not trigger consolidation; consolidator (Phase 2) picks up new events
    asynchronously.

    Args:
        conn: Active DB connection.
        persona_id: Target persona. Must exist in personas.
        counterparty_id: Optional counterparty.
        event_type: One of 'message_in', 'message_out', 'retrieval_trace', ...
        scope: 'private', 'shared', or 'public'.
        payload: Event body. Must be JSON-serializable.
        signature: Ed25519 signature of canonical_signing_message, base64.
        public_key_b64: The registered MCP public key for verification.
        idempotency_key: Unique per source. Prevents double-ingest.

    Returns:
        The persisted Event with assigned id and recorded_at.

    Raises:
        SignatureInvalid: Signature verification failed.
        IdempotencyConflict: Event with this key already exists.
    """
    content_hash = compute_content_hash(payload)

    # Verify signature before any write
    message = canonical_signing_message(persona_id, content_hash)
    verify(public_key_b64, message, signature)

    # Attempt insert; unique constraint on idempotency_key catches duplicates
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        cursor = await conn.execute(
            """
            INSERT INTO events
                (persona_id, counterparty_id, type, scope,
                 content_hash, idempotency_key, payload, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persona_id,
                counterparty_id,
                event_type,
                scope,
                content_hash,
                idempotency_key,
                payload_json,
                signature,
            ),
        )
        await conn.commit()
        event_id = cursor.lastrowid
        if event_id is None:
            msg = "INSERT did not return a rowid"
            raise RuntimeError(msg)
    except aiosqlite.IntegrityError as e:
        if "idempotency_key" in str(e).lower():
            raise IdempotencyConflict(
                f"Event with idempotency_key={idempotency_key!r} already exists"
            ) from e
        raise

    # Fetch the recorded_at assigned by the DB default
    retrieved = await get_event(conn, event_id)
    assert retrieved is not None
    return retrieved


async def get_event(conn: aiosqlite.Connection, event_id: int) -> Event | None:
    """Retrieve an event by id. Returns None if not found."""
    cursor = await conn.execute(
        """
        SELECT id, persona_id, counterparty_id, type, scope,
               content_hash, idempotency_key, payload, signature, recorded_at
        FROM events WHERE id = ?
        """,
        (event_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    return Event(
        id=row["id"],
        persona_id=row["persona_id"],
        counterparty_id=row["counterparty_id"],
        type=row["type"],
        scope=row["scope"],
        content_hash=row["content_hash"],
        idempotency_key=row["idempotency_key"],
        payload=json.loads(row["payload"]),
        signature=row["signature"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]).replace(tzinfo=UTC),
    )
