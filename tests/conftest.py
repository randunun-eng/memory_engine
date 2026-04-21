"""Shared pytest fixtures for all tests.

Fixtures defined here are available in every test module without explicit import.
Per-subdirectory conftest.py files extend this with fixtures specific to that
category (integration, invariants, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    import aiosqlite


# pytest-asyncio mode = "auto" in pyproject.toml means every async function
# is treated as a test. No need for @pytest.mark.asyncio decorators.


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Fresh SQLite DB with all migrations applied.

    Uses a tmp_path-based file (not :memory:) so that sqlite-vec extension
    loading works consistently. File is cleaned up by pytest's tmp_path
    lifecycle.

    Yields:
        An aiosqlite.Connection ready for use. Closed on teardown.
    """
    # Imports inside the fixture so top-of-file imports stay clean and
    # tests run even if the engine itself fails to import (reported clearly).
    from memory_engine.db.connection import connect
    from memory_engine.db.migrations import apply_all

    db_path = tmp_path / "test.db"
    conn = await connect(str(db_path))
    await apply_all(conn)

    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def seed_persona(db: aiosqlite.Connection):
    """A single seeded test persona with an Ed25519 keypair.

    Returns a TestPersona dataclass with id, slug, public_key_b64, and
    private_key bytes. The private key is test-only and must never appear
    in production code paths.
    """
    from tests.fixtures.personas import make_test_persona

    return await make_test_persona(db)


@pytest_asyncio.fixture
async def seed_counterparty(db: aiosqlite.Connection, seed_persona):
    """A seeded counterparty belonging to the seeded persona."""
    cursor = await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref, display_name) VALUES (?, ?, ?)",
        (seed_persona.id, "whatsapp:+1234567890", "Test Counterparty"),
    )
    await db.commit()
    cp_id = cursor.lastrowid
    assert cp_id is not None
    return cp_id


# ---- Markers -----------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    """Auto-skip eval/perf tests unless their flags are passed."""
    run_eval = config.getoption("--eval", default=False)
    run_perf = config.getoption("--perf", default=False)

    skip_eval = pytest.mark.skip(reason="eval tests skipped; use --eval to run")
    skip_perf = pytest.mark.skip(reason="perf tests skipped; use --perf to run")

    for item in items:
        if "eval" in item.keywords and not run_eval:
            item.add_marker(skip_eval)
        if "perf" in item.keywords and not run_perf:
            item.add_marker(skip_perf)


def pytest_addoption(parser):
    parser.addoption(
        "--eval",
        action="store_true",
        default=False,
        help="Run slow eval tests (requires real embedder + LLM)",
    )
    parser.addoption(
        "--perf",
        action="store_true",
        default=False,
        help="Run perf benchmark tests (10k+ neurons, takes ~30s)",
    )
