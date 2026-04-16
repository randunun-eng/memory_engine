"""Test persona factory.

Generates Ed25519 keypairs for test personas. The private key is returned to
the test so it can sign events; in production, private keys live only inside
MCP processes and are never accessible to the engine.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


@dataclass(frozen=True, slots=True)
class TestPersona:
    """A test persona with a freshly generated keypair.

    Attributes:
        id: persona row id.
        slug: the slug as stored in the personas table.
        public_key_b64: base64-encoded public key, as stored in mcp_sources.
        private_key: raw Ed25519 signing-key bytes. TEST ONLY. Never write to disk.
    """
    id: int
    slug: str
    public_key_b64: str
    private_key: bytes


async def make_test_persona(
    conn: aiosqlite.Connection,
    slug: str = "test_twin",
) -> TestPersona:
    """Insert a persona row and return its details with a freshly-generated keypair.

    Args:
        conn: Active DB connection (from the `db` fixture).
        slug: Unique slug for this persona. Tests using multiple personas must
            pass distinct slugs; the UNIQUE constraint on personas.slug
            enforces this.

    Returns:
        TestPersona with id, slug, public key (base64), and private key bytes.
    """
    # Import inside the function so import failures surface in the test
    # that needs the fixture rather than at collection time.
    from memory_engine.policy.signing import generate_keypair

    private_key, public_key = generate_keypair()
    public_key_b64 = base64.b64encode(public_key).decode("ascii")

    cursor = await conn.execute(
        "INSERT INTO personas (slug, identity_doc) VALUES (?, ?)",
        (slug, None),
    )
    await conn.commit()
    persona_id = cursor.lastrowid
    assert persona_id is not None, "INSERT did not return a row id"

    return TestPersona(
        id=persona_id,
        slug=slug,
        public_key_b64=public_key_b64,
        private_key=private_key,
    )
