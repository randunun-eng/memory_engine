# Phase 0 — Skeleton

> **Status:** Active
>
> **Duration:** 2 weeks (half-time solo)
>
> **Acceptance criterion:** `uv run pytest tests/integration/test_phase0.py tests/invariants/test_phase0.py -v` passes. A demo script in `examples/phase0_round_trip.py` ingests 10 events via the API and retrieves them, with content hashes matching.

---

## Goal

The event log works end to end. A signed event arrives, is verified, scope-classified, content-hashed, idempotency-checked, and durably appended. The same event is retrievable by ID with a stable hash. Every later phase depends on this being solid.

Phase 0 does not include: consolidation, embeddings, retrieval beyond direct-by-id, LLM calls, the policy plane, healing invariants, or any adapter. Those are Phases 1–5. Phase 0 is strictly the foundation.

---

## Prerequisites

- Repository scaffolding in place (CLAUDE.md, directory tree, blueprint documents).
- Python 3.12 and `uv` installed.
- SQLite 3.45+ with extension loading support.

Nothing code-wise. This is the first code phase.

---

## File manifest

### New files to create

#### Configuration

- `pyproject.toml` — package metadata, dependencies, tool configs.
- `config/default.toml` — runtime configuration defaults.
- `.env.example` — environment variable template.
- `.pre-commit-config.yaml` — secret scans, ruff, mypy.
- `.github/workflows/test.yml` — CI pipeline.

#### Source code

- `src/memory_engine/__init__.py` — version string, top-level package docstring.
- `src/memory_engine/config.py` — Pydantic Settings model.
- `src/memory_engine/exceptions.py` — exception hierarchy root.
- `src/memory_engine/db/connection.py` — async SQLite connection management.
- `src/memory_engine/db/migrations.py` — migration runner.
- `src/memory_engine/db/exceptions.py` — DB-layer exceptions.
- `src/memory_engine/policy/signing.py` — Ed25519 sign/verify.
- `src/memory_engine/core/events.py` — event append, retrieve, hash.
- `src/memory_engine/cli/main.py` — `memory-engine` CLI entry point.

#### Database

- `migrations/001_initial.sql` — initial schema.

#### Tests

- `tests/conftest.py` — shared fixtures.
- `tests/fixtures/personas.py` — test persona factory.
- `tests/integration/test_phase0.py` — integration tests.
- `tests/invariants/test_phase0.py` — invariant tests.

#### Examples

- `examples/phase0_round_trip.py` — demo script.

---

## pyproject.toml

```toml
[project]
name = "memory_engine"
version = "0.0.0"
description = "Reference implementation of the Wiki v3 neural memory orchestration blueprint."
readme = "README.md"
requires-python = ">=3.12"
license = { file = "LICENSE" }
authors = [{ name = "randunun-eng" }]

dependencies = [
    "aiosqlite>=0.20.0",
    "pynacl>=1.5.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "click>=8.1.0",
    "orjson>=3.10.0",
    "tomli-w>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "hypothesis>=6.110.0",
    "ruff>=0.6.0",
    "mypy>=1.11.0",
    "types-click",
]

[project.scripts]
memory-engine = "memory_engine.cli.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/memory_engine"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "B",    # flake8-bugbear
    "C4",   # flake8-comprehensions
    "S",    # flake8-bandit (security)
    "UP",   # pyupgrade
    "ASYNC", # flake8-async
]
ignore = [
    "S101",  # use of assert (allowed in tests)
    "E501",  # line-too-long (enforced softer via formatter)
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106"]  # allow asserts and hardcoded test passwords

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_configs = true

[[tool.mypy.overrides]]
module = ["nacl.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "--strict-markers -ra"
markers = [
    "eval: slow evaluation tests, excluded from default run",
]
```

## config/default.toml

```toml
[engine]
log_level = "INFO"
monthly_budget_usd = 0.0                # 0 = local only, no paid LLM calls

[db]
# Default: SQLite at data/engine.db
# Override with MEMORY_ENGINE_DB_URL env var for Postgres
backend = "sqlite"
path = "data/engine.db"

[embeddings]
model = "sentence-transformers/all-MiniLM-L6-v2"
dimensions = 384
revision = "sbert-minilm-l6-v2-1"

[grounding]
similarity_threshold = 0.40
llm_judge_required_for_tiers = ["semantic", "procedural"]

[working_memory]
capacity = 64
initial_activation = 1.0
decay_half_life_minutes = 30
```

## .env.example

```bash
# Required in production; ignored in tests
MEMORY_ENGINE_CONFIG=config/default.toml
MEMORY_ENGINE_VAULT_KEY=                    # 32-byte base64-encoded key
MEMORY_ENGINE_BACKUP_RECIPIENT=             # age recipient for encrypted backups

# Optional
LITELLM_BASE_URL=http://localhost:4000
LITELLM_API_KEY=
LOG_LEVEL=INFO
```

---

## Source modules

### `src/memory_engine/__init__.py`

```python
"""memory_engine — reference implementation of the Wiki v3 blueprint.

See CLAUDE.md at the repository root for architecture, governance rules,
and phase plan. Do not skip it.
"""

__version__ = "0.0.0"
```

### `src/memory_engine/exceptions.py`

```python
"""Exception hierarchy for memory_engine.

Every error raised by library code inherits from MemoryEngineError. Application
code can catch MemoryEngineError to handle our error domain; should never
catch Exception at function boundaries.
"""


class MemoryEngineError(Exception):
    """Root of all memory_engine exceptions. Never raise this directly."""


class ConfigError(MemoryEngineError):
    """Configuration problem at load or startup."""


class SignatureInvalid(MemoryEngineError):
    """Signature verification failed."""


class IdempotencyConflict(MemoryEngineError):
    """Event with this idempotency key already exists."""


class InvariantViolation(MemoryEngineError):
    """A governance invariant was violated.

    Subclasses indicate severity. Critical violations halt the system.
    """


class ScopeViolation(InvariantViolation):
    """Scope mismatch detected. Always critical."""


class CrossCounterpartyLeak(InvariantViolation):
    """Cross-counterparty data exposure detected. Always critical."""
```

### `src/memory_engine/config.py`

```python
"""Runtime configuration. Loaded once at startup; imported everywhere else."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBSettings(BaseSettings):
    backend: Literal["sqlite", "postgres"] = "sqlite"
    path: str = "data/engine.db"            # for sqlite
    url: str | None = None                   # for postgres (e.g. postgresql+asyncpg://...)


class EmbeddingSettings(BaseSettings):
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dimensions: int = 384
    revision: str = "sbert-minilm-l6-v2-1"


class GroundingSettings(BaseSettings):
    similarity_threshold: float = 0.40
    llm_judge_required_for_tiers: list[str] = Field(default_factory=lambda: ["semantic", "procedural"])


class WorkingMemorySettings(BaseSettings):
    capacity: int = 64
    initial_activation: float = 1.0
    decay_half_life_minutes: int = 30


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMORY_ENGINE_",
        env_nested_delimiter="__",
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    monthly_budget_usd: float = 0.0
    vault_key: SecretStr | None = None
    backup_recipient: str | None = None

    db: DBSettings = Field(default_factory=DBSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    grounding: GroundingSettings = Field(default_factory=GroundingSettings)
    working_memory: WorkingMemorySettings = Field(default_factory=WorkingMemorySettings)

    @classmethod
    def load(cls, config_path: str | Path = "config/default.toml") -> "Settings":
        """Load settings from TOML + env vars. Env vars take precedence."""
        import tomllib
        config_path = Path(config_path)
        if config_path.exists():
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        else:
            data = {}
        return cls(**data)


# Module-level singleton, loaded on first import
settings = Settings.load()
```

### `src/memory_engine/db/connection.py`

```python
"""Async SQLite connection management.

Single-writer-per-table invariant (rule 9) is enforced by using one connection
for writes. Reads can share the connection or use a separate read connection;
this module exposes a single shared connection factory for Phase 0.
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from memory_engine.config import settings


async def connect(db_path: str | None = None) -> aiosqlite.Connection:
    """Open an async SQLite connection with WAL, foreign keys, and extension loading.

    Args:
        db_path: Override the configured DB path (used in tests).

    Returns:
        An aiosqlite.Connection ready for use. Caller is responsible for closing.
    """
    path = db_path or settings.db.path
    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")
    conn.row_factory = aiosqlite.Row
    return conn
```

### `src/memory_engine/db/exceptions.py`

```python
"""DB-layer exceptions."""

from memory_engine.exceptions import MemoryEngineError


class MigrationError(MemoryEngineError):
    """Migration failed or checksum mismatch."""


class UpdateForbidden(MemoryEngineError):
    """Attempted UPDATE on immutable table. Rule 1."""


class DeleteForbidden(MemoryEngineError):
    """Attempted DELETE on immutable table. Rule 1."""
```

### `src/memory_engine/db/migrations.py`

```python
"""Migration runner.

Applies SQL files in order from migrations/. Records applied migrations in
schema_migrations with checksum. Detects if a migration file was edited after
apply (forbidden).
"""

import hashlib
from pathlib import Path

import aiosqlite

from memory_engine.db.exceptions import MigrationError

MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


async def apply_all(conn: aiosqlite.Connection, migrations_dir: Path | None = None) -> list[str]:
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
    applied = {row["name"]: row["checksum"] async for row in cursor}

    newly_applied = []
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
        await conn.executescript(sql_text)
        await conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
            (version, name, checksum),
        )
        await conn.commit()
        newly_applied.append(name)

    return newly_applied


async def migration_status(conn: aiosqlite.Connection) -> list[dict]:
    """Return applied migrations with versions and timestamps."""
    cursor = await conn.execute(
        "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version"
    )
    return [dict(row) async for row in cursor]
```

### `src/memory_engine/policy/signing.py`

```python
"""Ed25519 signing for MCP sources.

Keypair generation is a one-time operator action, not done by the engine in
production. Test fixtures generate keys as needed.
"""

import base64

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from memory_engine.exceptions import SignatureInvalid


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair. Returns (private_key_bytes, public_key_bytes).

    Used at MCP registration time. The private key is shown once to the operator
    and never stored by the engine.
    """
    signing_key = SigningKey.generate()
    return bytes(signing_key), bytes(signing_key.verify_key)


def sign(private_key: bytes, message: bytes) -> str:
    """Sign a message. Returns base64-encoded signature."""
    signing_key = SigningKey(private_key)
    signed = signing_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> None:
    """Verify a signature. Raises SignatureInvalid on failure.

    Args:
        public_key_b64: Base64-encoded public key, as stored in mcp_sources.
        message: The signed bytes. Typically canonical form of (persona_id, content_hash).
        signature_b64: Base64-encoded signature.

    Raises:
        SignatureInvalid: If the signature does not verify.
    """
    try:
        public_key = base64.b64decode(public_key_b64)
        signature = base64.b64decode(signature_b64)
        verify_key = VerifyKey(public_key)
        verify_key.verify(message, signature)
    except BadSignatureError as e:
        raise SignatureInvalid(f"Signature verification failed") from e
    except ValueError as e:
        raise SignatureInvalid(f"Invalid key or signature encoding: {e}") from e


def canonical_signing_message(persona_id: int, content_hash: str) -> bytes:
    """Canonical bytes to sign for an event.

    The MCP signs (persona_id || content_hash). The engine verifies against
    the same canonical form. Any change here requires coordination with every
    MCP; treat as contract.
    """
    return f"{persona_id}:{content_hash}".encode("utf-8")
```

### `src/memory_engine/core/events.py`

```python
"""Event log append, retrieve, and hash.

The event log is the only source of truth (principle 1). Events are immutable
(rule 1). Every event carries a signature that must verify at ingress.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import aiosqlite

from memory_engine.exceptions import IdempotencyConflict, SignatureInvalid
from memory_engine.policy.signing import canonical_signing_message, verify

Scope = Literal["private", "shared", "public"]
EventType = Literal["message_in", "message_out", "retrieval_trace", "prompt_promoted", "operator_action"]


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
        counterparty_id: Optional counterparty. Required for message_in/message_out
            from a named external entity.
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
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                signature,
            ),
        )
        await conn.commit()
        event_id = cursor.lastrowid
        if event_id is None:
            raise RuntimeError("INSERT did not return a rowid")
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
```

### `src/memory_engine/cli/main.py`

```python
"""memory-engine CLI entry point.

Phase 0: db migrate, db status. Later phases add doctor, prompt, heal, etc.
"""

import asyncio

import click

from memory_engine.db.connection import connect
from memory_engine.db.migrations import apply_all, migration_status


@click.group()
def main() -> None:
    """memory_engine CLI."""


@main.group()
def db() -> None:
    """Database operations."""


@db.command("migrate")
def db_migrate() -> None:
    """Apply pending migrations."""

    async def _run() -> None:
        conn = await connect()
        try:
            applied = await apply_all(conn)
            if applied:
                click.echo(f"Applied: {', '.join(applied)}")
            else:
                click.echo("No pending migrations.")
        finally:
            await conn.close()

    asyncio.run(_run())


@db.command("status")
def db_status() -> None:
    """Show applied migrations."""

    async def _run() -> None:
        conn = await connect()
        try:
            rows = await migration_status(conn)
            if not rows:
                click.echo("No migrations applied.")
                return
            for row in rows:
                click.echo(f"  {row['version']:>3}  {row['name']:<40}  {row['applied_at']}")
        finally:
            await conn.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

---

## Migration

### `migrations/001_initial.sql`

See `docs/SCHEMA.md` → Migration 001 for the full DDL. The file contains, in order:

1. `schema_migrations` — bootstrapped by the migration runner, not by this file.
2. `personas` table.
3. `counterparties` table.
4. `events` table + indexes + immutability triggers.
5. `neurons` table + indexes + CHECK constraints.
6. `neurons_vec` virtual table (sqlite-vec).

Exact SQL:

```sql
-- Migration 001: Initial schema
-- Phase 0 (Skeleton)

CREATE TABLE personas (
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  identity_doc    TEXT,
  version         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE counterparties (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  external_ref    TEXT NOT NULL,
  display_name    TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (persona_id, external_ref)
);

CREATE TABLE events (
  id              INTEGER PRIMARY KEY,
  persona_id      INTEGER NOT NULL REFERENCES personas(id),
  counterparty_id INTEGER REFERENCES counterparties(id),
  type            TEXT NOT NULL,
  scope           TEXT NOT NULL CHECK (scope IN ('private', 'shared', 'public')),
  content_hash    TEXT NOT NULL,
  idempotency_key TEXT,
  payload         TEXT NOT NULL,
  signature       TEXT NOT NULL,
  mcp_source_id   INTEGER,
  recorded_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (idempotency_key)
);

CREATE INDEX ix_events_persona_recorded ON events(persona_id, recorded_at);
CREATE INDEX ix_events_counterparty
  ON events(counterparty_id) WHERE counterparty_id IS NOT NULL;
CREATE INDEX ix_events_type_persona ON events(persona_id, type);

-- Rule 1: events are immutable. Triggers enforce at the DB layer.
CREATE TRIGGER events_immutable_update
BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are immutable (rule 1)');
END;

CREATE TRIGGER events_immutable_delete
BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events are immutable (rule 1)');
END;

CREATE TABLE neurons (
  id                      INTEGER PRIMARY KEY,
  persona_id              INTEGER NOT NULL REFERENCES personas(id),
  counterparty_id         INTEGER REFERENCES counterparties(id),
  kind                    TEXT NOT NULL CHECK (kind IN ('self_fact', 'counterparty_fact', 'domain_fact')),
  content                 TEXT NOT NULL,
  content_hash            TEXT NOT NULL,
  source_event_ids        TEXT NOT NULL,
  source_count            INTEGER NOT NULL DEFAULT 1,
  distinct_source_count   INTEGER NOT NULL DEFAULT 1,
  tier                    TEXT NOT NULL CHECK (tier IN ('working', 'episodic', 'semantic', 'procedural')),
  t_valid_start           TEXT,
  t_valid_end             TEXT,
  recorded_at             TEXT NOT NULL DEFAULT (datetime('now')),
  superseded_at           TEXT,
  superseded_by           INTEGER REFERENCES neurons(id),
  embedder_rev            TEXT NOT NULL,
  version                 INTEGER NOT NULL DEFAULT 1,

  CHECK (
    (kind = 'counterparty_fact' AND counterparty_id IS NOT NULL)
    OR (kind IN ('self_fact', 'domain_fact') AND counterparty_id IS NULL)
  )
);

CREATE INDEX ix_neurons_persona_kind_active
  ON neurons(persona_id, kind) WHERE superseded_at IS NULL;
CREATE INDEX ix_neurons_counterparty_active
  ON neurons(counterparty_id) WHERE counterparty_id IS NOT NULL AND superseded_at IS NULL;
CREATE INDEX ix_neurons_embedder
  ON neurons(embedder_rev) WHERE superseded_at IS NULL;

-- sqlite-vec virtual table for vector retrieval (Phase 1 starts using this)
CREATE VIRTUAL TABLE neurons_vec USING vec0(
  neuron_id INTEGER PRIMARY KEY,
  embedding FLOAT[384]
);
```

If `sqlite-vec` is not loaded at runtime, the `neurons_vec` CREATE will fail. Add to connection setup:

```python
await conn.enable_load_extension(True)
await conn.load_extension("vec0")      # from sqlite-vec install
await conn.enable_load_extension(False)
```

Document this in `docs/runbooks/sqlite_vec_install.md` (Phase 0 follow-up).

---

## Tests

### `tests/conftest.py`

```python
"""Shared fixtures for all tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from memory_engine.db.connection import connect
from memory_engine.db.migrations import apply_all


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Fresh SQLite DB with all migrations applied."""
    db_path = tmp_path / "test.db"
    conn = await connect(str(db_path))
    await apply_all(conn)
    try:
        yield conn
    finally:
        await conn.close()
```

### `tests/fixtures/personas.py`

```python
"""Test persona factory. Generates Ed25519 keypairs for signing."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from memory_engine.policy.signing import generate_keypair


@dataclass(frozen=True, slots=True)
class TestPersona:
    id: int
    slug: str
    public_key_b64: str
    private_key: bytes


async def seed_persona(conn, slug: str = "test_twin") -> TestPersona:
    """Insert a persona and return its details with freshly-generated keys."""
    priv, pub = generate_keypair()
    pub_b64 = base64.b64encode(pub).decode("ascii")

    cursor = await conn.execute(
        "INSERT INTO personas (slug, identity_doc) VALUES (?, ?)",
        (slug, None),
    )
    await conn.commit()
    persona_id = cursor.lastrowid
    assert persona_id is not None

    return TestPersona(
        id=persona_id,
        slug=slug,
        public_key_b64=pub_b64,
        private_key=priv,
    )
```

### `tests/integration/test_phase0.py`

```python
"""Phase 0 integration tests.

Acceptance:
- test_schema_applies_clean
- test_event_round_trip
- test_idempotency_key_rejects_duplicate
- test_signature_verification_rejects_bad
- test_persona_slug_unique
"""

from __future__ import annotations

import pytest

from memory_engine.core.events import append_event, compute_content_hash, get_event
from memory_engine.db.migrations import migration_status
from memory_engine.exceptions import IdempotencyConflict, SignatureInvalid
from memory_engine.policy.signing import canonical_signing_message, sign
from tests.fixtures.personas import seed_persona


async def test_schema_applies_clean(db) -> None:
    """All phase 0 tables exist after migrations run."""
    rows = await migration_status(db)
    names = [r["name"] for r in rows]
    assert "001_initial" in names

    # Spot-check the key tables
    for table in ["personas", "counterparties", "events", "neurons"]:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        assert await cursor.fetchone() is not None, f"Table {table} missing"


async def test_event_round_trip(db) -> None:
    """Append one event, retrieve by id, verify hash stable."""
    persona = await seed_persona(db)
    payload = {"text": "hello world", "channel": "test"}
    content_hash = compute_content_hash(payload)
    signature = sign(persona.private_key, canonical_signing_message(persona.id, content_hash))

    event = await append_event(
        db,
        persona_id=persona.id,
        counterparty_id=None,
        event_type="message_in",
        scope="private",
        payload=payload,
        signature=signature,
        public_key_b64=persona.public_key_b64,
        idempotency_key="test-1",
    )
    assert event.content_hash == content_hash
    assert event.payload == payload

    retrieved = await get_event(db, event.id)
    assert retrieved is not None
    assert retrieved.content_hash == event.content_hash
    assert retrieved.payload == payload


async def test_idempotency_key_rejects_duplicate(db) -> None:
    """Second append with same idempotency_key raises IdempotencyConflict."""
    persona = await seed_persona(db)
    payload = {"text": "hello"}
    ch = compute_content_hash(payload)
    sig = sign(persona.private_key, canonical_signing_message(persona.id, ch))

    await append_event(
        db, persona_id=persona.id, counterparty_id=None,
        event_type="message_in", scope="private", payload=payload,
        signature=sig, public_key_b64=persona.public_key_b64,
        idempotency_key="dupe-key",
    )

    with pytest.raises(IdempotencyConflict):
        await append_event(
            db, persona_id=persona.id, counterparty_id=None,
            event_type="message_in", scope="private", payload=payload,
            signature=sig, public_key_b64=persona.public_key_b64,
            idempotency_key="dupe-key",
        )


async def test_signature_verification_rejects_bad(db) -> None:
    """Tampered signature is rejected before any DB write."""
    persona = await seed_persona(db)
    payload = {"text": "hi"}
    ch = compute_content_hash(payload)
    good_sig = sign(persona.private_key, canonical_signing_message(persona.id, ch))
    bad_sig = good_sig[:-4] + "AAAA"  # corrupt last 4 chars

    with pytest.raises(SignatureInvalid):
        await append_event(
            db, persona_id=persona.id, counterparty_id=None,
            event_type="message_in", scope="private", payload=payload,
            signature=bad_sig, public_key_b64=persona.public_key_b64,
            idempotency_key="bad-sig",
        )

    # No event was written
    cursor = await db.execute("SELECT count(*) AS c FROM events")
    row = await cursor.fetchone()
    assert row["c"] == 0


async def test_persona_slug_unique(db) -> None:
    """Two personas cannot share a slug."""
    await seed_persona(db, slug="shared_slug")
    with pytest.raises(Exception):  # aiosqlite.IntegrityError
        await seed_persona(db, slug="shared_slug")
```

### `tests/invariants/test_phase0.py`

```python
"""Phase 0 invariant tests.

Governance rules enforced:
- Rule 1: events immutable
- Rule 14: content hash determinism (used by neurons.source_event_ids later)
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.policy.signing import canonical_signing_message, sign
from tests.fixtures.personas import seed_persona


async def test_event_update_is_forbidden(db) -> None:
    """Rule 1: events are immutable at the DB layer.

    A direct UPDATE must fail via the trigger, not rely on Python discipline.
    """
    persona = await seed_persona(db)
    payload = {"text": "hello"}
    ch = compute_content_hash(payload)
    sig = sign(persona.private_key, canonical_signing_message(persona.id, ch))
    event = await append_event(
        db, persona_id=persona.id, counterparty_id=None,
        event_type="message_in", scope="private", payload=payload,
        signature=sig, public_key_b64=persona.public_key_b64,
        idempotency_key="rule1-update",
    )

    with pytest.raises(Exception) as exc:  # SQLite raises OperationalError on trigger ABORT
        await db.execute(
            "UPDATE events SET payload = ? WHERE id = ?",
            ('{"tampered": true}', event.id),
        )
    assert "immutable" in str(exc.value).lower() or "rule 1" in str(exc.value).lower()


async def test_event_delete_is_forbidden(db) -> None:
    """Rule 1: deletion is as forbidden as update."""
    persona = await seed_persona(db)
    payload = {"text": "goodbye"}
    ch = compute_content_hash(payload)
    sig = sign(persona.private_key, canonical_signing_message(persona.id, ch))
    event = await append_event(
        db, persona_id=persona.id, counterparty_id=None,
        event_type="message_in", scope="private", payload=payload,
        signature=sig, public_key_b64=persona.public_key_b64,
        idempotency_key="rule1-delete",
    )

    with pytest.raises(Exception) as exc:
        await db.execute("DELETE FROM events WHERE id = ?", (event.id,))
    assert "immutable" in str(exc.value).lower() or "rule 1" in str(exc.value).lower()


@given(
    content=st.dictionaries(
        keys=st.text(min_size=1, max_size=50),
        values=st.one_of(
            st.text(max_size=200),
            st.integers(),
            st.booleans(),
            st.none(),
        ),
        min_size=1,
        max_size=10,
    )
)
def test_content_hash_is_deterministic(content: dict) -> None:
    """Hash of the same content is stable across calls.

    Property-based: any plausible payload hashes identically twice in a row.
    """
    h1 = compute_content_hash(content)
    h2 = compute_content_hash(content)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex
```

---

## Example script

### `examples/phase0_round_trip.py`

```python
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
        "INSERT INTO personas (slug) VALUES (?)", ("demo_persona",),
    )
    await conn.commit()
    persona_id = cursor.lastrowid
    assert persona_id is not None

    # Append 10 events
    event_ids = []
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
        print(f"Appended event {event.id}: hash={event.content_hash[:16]}...")

    # Read them back
    for eid in event_ids:
        retrieved = await get_event(conn, eid)
        assert retrieved is not None
        print(f"  {eid}: {retrieved.payload['text']!r}")

    await conn.close()
    print(f"\nRound-trip OK. {len(event_ids)} events written and retrieved.")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Acceptance criterion (in detail)

Phase 0 is complete when **all** of the following hold:

1. `uv sync` completes without error on a fresh clone.
2. `uv run ruff check` passes.
3. `uv run mypy src/` passes.
4. `uv run memory-engine db migrate` creates the DB, applies `001_initial`, and reports `Applied: 001_initial`.
5. `uv run memory-engine db status` shows `001_initial` applied.
6. `uv run pytest tests/integration/test_phase0.py tests/invariants/test_phase0.py -v` passes all tests.
7. `uv run python examples/phase0_round_trip.py` completes, writing 10 events and reading them back with matching hashes.
8. CI workflow (`.github/workflows/test.yml`) runs all of the above on push and passes.

Update `CLAUDE.md` §8 — Current Focus to indicate Phase 0 complete, Phase 1 next.

---

## Out of scope for this phase

- Consolidation. No working memory yet. No episodic / semantic / procedural promotion.
- Embeddings. The `embedder_rev` column exists but nothing populates `neurons_vec`.
- Retrieval. Direct by-id lookup only. No BM25, no vector, no lens, no RRF.
- LLM calls. No classifier, no extractor, no contradiction judge. The policy plane module is not created in Phase 0.
- Healing invariants. Rule 1 is enforced by DB trigger (hard), but there's no healer loop yet.
- Identity document parsing. The column exists; nothing reads it.
- WhatsApp adapter. MCP sources table doesn't exist yet (migration 005).
- Grounding gate. Phase 2.
- Observability. Basic logging is fine; Prometheus metrics come in Phase 6.

If scope expands during implementation, stop and raise in `docs/blueprint/DRIFT.md` first.

---

## Common pitfalls

**Forgetting sqlite-vec.** The migration will fail at `CREATE VIRTUAL TABLE neurons_vec`. Install: `pip install sqlite-vec` or use the bundled `.so` for your platform from https://github.com/asg017/sqlite-vec/releases. The connection must load the extension before running migrations. Put the load in `src/memory_engine/db/connection.py::connect()`.

**Hash inconsistency.** If `compute_content_hash` is not called on canonical JSON, the same logical payload can produce different hashes (key order, whitespace, encoding). Always use `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` and UTF-8 bytes.

**Signature verification timing.** Verify before any write. If verification happens after insert and fails, you've already corrupted the log — and since events are immutable, the bad row stays forever. The flow in `append_event` verifies first, inserts second.

**Idempotency key scope.** The UNIQUE constraint is global. If two personas use the same source-format key by coincidence, one will be rejected. Either scope the key by `(persona_id, idempotency_key)` with a composite UNIQUE, or require callers to namespace their keys (e.g. `whatsapp:+123:msg_id`). Phase 0 goes with caller namespacing; document this in the adapter spec for Phase 5.

**Timestamp timezone.** SQLite `datetime('now')` returns UTC with no `Z` suffix. `datetime.fromisoformat` on that string returns a naive datetime. Always `.replace(tzinfo=UTC)` after parsing, or add `'T'` and `'+00:00'` to the stored format.

**Test fixture leaking state.** The `db` fixture in `conftest.py` uses `tmp_path`, so each test gets a fresh database. If you see test interdependencies, it's a fixture bug. Never put persistent state in module-level variables.

**Async/sync mixing.** The CLI uses `asyncio.run()` to bridge. Do not mix `asyncio.run()` at multiple levels or you get RuntimeError. The CLI has exactly one `asyncio.run` per command; everything inside is `async def`.

---

## When Phase 0 closes

Merge the PR that implements Phase 0. Tag the commit: `git tag phase-0-complete`. Update `CLAUDE.md` §8 to point to Phase 1. Open the Phase 1 document and begin.

Commit message for the final Phase 0 PR: `feat(phase0): event log round-trip passes acceptance`.
