"""Group message handling.

A WhatsApp group maps to a single counterparty. Individual senders
within the group are stored as sender_hint on the event, but never
used in retrieval queries. Alice-in-group and Alice-in-1:1 are two
separate counterparties.

This is the price of the group-as-counterparty simplification:
retrieval cannot distinguish "what Alice said in the group" from
"what Bob said in the group." Both are "messages in the Acme Team
counterparty." Cross-referencing requires the audited admin path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.adapters.whatsapp.canonicalize import (
    canonicalize_group_jid,
    canonicalize_phone,
)


async def lookup_or_create_group_counterparty(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    group_jid: str,
    display_name_hint: str | None = None,
) -> int:
    """Find or create a counterparty for a WhatsApp group.

    Args:
        conn: Database connection.
        persona_id: The persona receiving group messages.
        group_jid: Raw group JID (will be canonicalized).
        display_name_hint: Optional group name from WhatsApp metadata.

    Returns:
        counterparty_id for the group.
    """
    external_ref = canonicalize_group_jid(group_jid)

    cursor = await conn.execute(
        "SELECT id FROM counterparties WHERE persona_id = ? AND external_ref = ?",
        (persona_id, external_ref),
    )
    row = await cursor.fetchone()

    if row is not None:
        return int(row["id"])

    # Create new group counterparty
    cursor = await conn.execute(
        """
        INSERT INTO counterparties (persona_id, external_ref, display_name)
        VALUES (?, ?, ?)
        """,
        (persona_id, external_ref, display_name_hint),
    )
    await conn.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def prepare_sender_hint(sender_phone: str | None) -> str | None:
    """Canonicalize a group sender's phone for use as sender_hint.

    The sender_hint is metadata only — it does NOT create a
    sub-counterparty and does NOT influence retrieval. It exists
    for audit and future admin queries.

    Returns:
        Canonical "whatsapp:+<E164>" string, or None if no sender provided.
    """
    if sender_phone is None:
        return None
    return canonicalize_phone(sender_phone)
