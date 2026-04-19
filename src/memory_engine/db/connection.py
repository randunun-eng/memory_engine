"""Async SQLite connection management.

Single-writer-per-table invariant (rule 9) is enforced by using one connection
for writes. Reads can share the connection or use a separate read connection;
this module exposes a single shared connection factory for Phase 0.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


async def connect(db_path: str | None = None) -> aiosqlite.Connection:
    """Open an async SQLite connection with WAL, foreign keys, and sqlite-vec.

    Args:
        db_path: Override the configured DB path (used in tests).

    Returns:
        An aiosqlite.Connection ready for use. Caller is responsible for closing.
    """
    if db_path is None:
        from memory_engine.config import get_settings

        db_path = get_settings().db.path

    # Ensure parent directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(db_path, timeout=30.0)
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")
    # Docker Desktop macOS bind-mount + SQLite deadlocks under concurrent
    # writers. busy_timeout lets SQLite retry internally for up to 30s
    # rather than surfacing "disk I/O error" / "database is locked" on
    # the first contended write. See DRIFT `sqlite-index-corruption-during-live-writes`.
    await conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = aiosqlite.Row

    # Load sqlite-vec for the neurons_vec virtual table
    try:
        await conn.enable_load_extension(True)
        import sqlite_vec

        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
    except Exception:
        logger.debug("sqlite-vec extension not available; neurons_vec will not work")

    return conn
