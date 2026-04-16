"""Demo: seed a persona, ingest 10 events, read them back.

Run after `uv run memory-engine db migrate`.

Usage:
    uv run python examples/phase0_round_trip.py
"""

from __future__ import annotations

import asyncio
import base64

from memory_engine.core.events import append_event, compute_content_hash, get_event
from memory_engine.db.connection import connect
from memory_engine.db.migrations import apply_all
from memory_engine.policy.signing import canonical_signing_message, generate_keypair, sign


async def main() -> None:
    conn = await connect()
    await apply_all(conn)

    # Create a persona
    priv, pub = generate_keypair()
    pub_b64 = base64.b64encode(pub).decode("ascii")
    cursor = await conn.execute(
        "INSERT INTO personas (slug) VALUES (?)",
        ("demo_persona",),
    )
    await conn.commit()
    persona_id = cursor.lastrowid
    assert persona_id is not None  # noqa: S101

    # Append 10 events
    event_ids: list[int] = []
    for i in range(10):
        payload = {"text": f"message {i}", "channel": "demo"}
        ch = compute_content_hash(payload)
        sig = sign(priv, canonical_signing_message(persona_id, ch))
        event = await append_event(
            conn,
            persona_id=persona_id,
            counterparty_id=None,
            event_type="message_in",
            scope="private",
            payload=payload,
            signature=sig,
            public_key_b64=pub_b64,
            idempotency_key=f"demo-{i}",
        )
        event_ids.append(event.id)
        print(f"Appended event {event.id}: hash={event.content_hash[:16]}...")  # noqa: T201

    # Read them back
    for eid in event_ids:
        retrieved = await get_event(conn, eid)
        assert retrieved is not None  # noqa: S101
        print(f"  {eid}: {retrieved.payload['text']!r}")  # noqa: T201

    await conn.close()
    print(f"\nRound-trip OK. {len(event_ids)} events written and retrieved.")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
