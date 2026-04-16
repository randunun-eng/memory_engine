"""Migration runner.

Applies SQL files in order from migrations/. Records applied migrations in
schema_migrations with checksum. Detects if a migration file was edited after
apply (forbidden).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from memory_engine.db.exceptions import MigrationError

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


async def apply_all(
    conn: aiosqlite.Connection,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply all pending migrations. Returns the list of applied names.

    Creates schema_migrations if not present. Applies each *.sql in alphabetical
    order that is not yet in schema_migrations. Computes and stores checksum.

    Raises MigrationError if an already-applied migration's file content has
    changed since application (checksum mismatch).
    """
    migrations_dir = migrations_dir or MIGRATIONS_DIR

    # Bootstrap the tracking table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            checksum   TEXT NOT NULL
        )
        """
    )
    await conn.commit()

    # Find all migration files
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        return []

    # Get already-applied
    cursor = await conn.execute("SELECT version, name, checksum FROM schema_migrations")
    applied: dict[str, str] = {row["name"]: row["checksum"] async for row in cursor}

    newly_applied: list[str] = []
    for mf in migration_files:
        name = mf.stem
        # Parse version from filename prefix (e.g. '001_initial')
        version_str = name.split("_")[0]
        try:
            version = int(version_str)
        except ValueError:
            continue  # Skip non-numbered files like README

        sql_text = mf.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()

        if name in applied:
            # Already applied; verify checksum
            if applied[name] != checksum:
                raise MigrationError(
                    f"Migration {name} checksum mismatch. "
                    f"File was edited after application. "
                    f"Expected {applied[name][:16]}..., got {checksum[:16]}..."
                )
            continue

        # Apply new migration
        logger.info("Applying migration %s", name)
        await conn.executescript(sql_text)
        await conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
            (version, name, checksum),
        )
        await conn.commit()
        newly_applied.append(name)

    return newly_applied


async def migration_status(conn: aiosqlite.Connection) -> list[dict[str, object]]:
    """Return applied migrations with versions and timestamps."""
    cursor = await conn.execute(
        "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version"
    )
    return [dict(row) async for row in cursor]
