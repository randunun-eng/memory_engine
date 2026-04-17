"""MCP source registration and token management.

Each WhatsApp MCP is registered once per persona. Registration produces
a bearer token (shown once to the operator) and stores the public key
for signature verification. The token authenticates API requests; the
public key verifies event signatures.

Token lifecycle:
  register → token issued, shown once
  rotate   → new token issued, old revoked
  revoke   → mcp_source marked inactive

The MCP is untrusted code with privileged write access. It can ingest
events for its bound persona only. It cannot query memory (no recall
token). See blueprint §7 for the full trust model.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.exceptions import ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MCPSource:
    """A registered MCP source."""

    id: int
    persona_id: int
    kind: str
    name: str
    public_key_ed25519: str
    token_hash: str
    registered_at: str
    revoked_at: str | None


def _hash_token(token: str) -> str:
    """SHA-256 hash of a bearer token for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def register_mcp(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    kind: str,
    name: str,
    public_key_b64: str,
) -> tuple[MCPSource, str]:
    """Register a new MCP source for a persona.

    Generates a bearer token, stores its hash, and returns both the
    MCPSource and the plaintext token (shown once to the operator).

    Args:
        conn: Database connection.
        persona_id: The persona this MCP is bound to.
        kind: Adapter kind (e.g. "whatsapp").
        name: Unique name for this MCP within the persona.
        public_key_b64: Base64-encoded Ed25519 public key.

    Returns:
        Tuple of (MCPSource, bearer_token_plaintext).

    Raises:
        ConfigError: If registration fails (e.g. duplicate name).
    """
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)

    try:
        cursor = await conn.execute(
            """
            INSERT INTO mcp_sources
                (persona_id, kind, name, public_key_ed25519, token_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (persona_id, kind, name, public_key_b64, token_hash),
        )
        await conn.commit()
    except Exception as e:
        if "UNIQUE" in str(e).upper():
            raise ConfigError(
                f"MCP source {name!r} already registered for persona {persona_id}"
            ) from e
        raise

    mcp_id = cursor.lastrowid
    assert mcp_id is not None

    mcp = await get_mcp_source(conn, mcp_id)
    assert mcp is not None

    logger.info(
        "Registered MCP source %s (id=%d) for persona %d",
        name,
        mcp_id,
        persona_id,
    )

    return mcp, token


async def get_mcp_source(
    conn: aiosqlite.Connection,
    mcp_id: int,
) -> MCPSource | None:
    """Fetch an MCP source by id."""
    cursor = await conn.execute(
        """
        SELECT id, persona_id, kind, name, public_key_ed25519,
               token_hash, registered_at, revoked_at
        FROM mcp_sources WHERE id = ?
        """,
        (mcp_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    return MCPSource(
        id=row["id"],
        persona_id=row["persona_id"],
        kind=row["kind"],
        name=row["name"],
        public_key_ed25519=row["public_key_ed25519"],
        token_hash=row["token_hash"],
        registered_at=row["registered_at"],
        revoked_at=row["revoked_at"],
    )


async def resolve_token(
    conn: aiosqlite.Connection,
    bearer_token: str,
) -> MCPSource | None:
    """Resolve a bearer token to an active MCP source.

    Returns None if the token is invalid, revoked, or unknown.
    """
    token_hash = _hash_token(bearer_token)

    cursor = await conn.execute(
        """
        SELECT id, persona_id, kind, name, public_key_ed25519,
               token_hash, registered_at, revoked_at
        FROM mcp_sources
        WHERE token_hash = ? AND revoked_at IS NULL
        """,
        (token_hash,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    return MCPSource(
        id=row["id"],
        persona_id=row["persona_id"],
        kind=row["kind"],
        name=row["name"],
        public_key_ed25519=row["public_key_ed25519"],
        token_hash=row["token_hash"],
        registered_at=row["registered_at"],
        revoked_at=row["revoked_at"],
    )


async def revoke_mcp(
    conn: aiosqlite.Connection,
    mcp_id: int,
) -> None:
    """Revoke an MCP source. Sets revoked_at; does not delete."""
    await conn.execute(
        "UPDATE mcp_sources SET revoked_at = datetime('now') WHERE id = ?",
        (mcp_id,),
    )
    await conn.commit()
    logger.info("Revoked MCP source id=%d", mcp_id)
