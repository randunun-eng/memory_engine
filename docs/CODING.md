# Coding Standards

> This document is the detailed version of CLAUDE.md §10. When in doubt, CLAUDE.md wins.

## Python

### Version and tooling

- **Python 3.12.** No exceptions. Type hints, match statements, pattern matching — all required-worthy.
- **uv** for dependency management. Lockfile (`uv.lock`) is committed.
- **ruff** for linting and import sorting. Config in `pyproject.toml`. `uv run ruff check` on every PR.
- **mypy** in strict mode. `uv run mypy src/` must pass.
- **pytest + pytest-asyncio** for testing. Never `unittest`.

### Type hints

Required on:
- Every public function signature (arguments and return type).
- Every dataclass / Pydantic model field.
- Every module-level constant.

Not required on:
- Private helpers (leading underscore).
- Lambdas.
- Test functions (but return type `None` is conventional).

Use modern syntax: `list[int]`, not `List[int]`. `str | None`, not `Optional[str]`. `dict[str, Any]`, not `Dict[str, Any]`. Python 3.12 natively supports these.

### Async everywhere

I/O-bound code is async. Database calls, HTTP calls, LLM calls, file reads. No `time.sleep`; use `asyncio.sleep`. No `requests`; use `httpx.AsyncClient`. No `psycopg2`; use `asyncpg`.

CPU-bound work that blocks the loop (embedding generation, cryptographic verification on large payloads) runs in an executor:

```python
async def embed_async(text: str) -> list[float]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_sync, text)
```

Never call sync functions from async code without awareness of the blocking cost.

### Dataclasses vs Pydantic

- **Dataclasses** for internal value objects. Simpler, faster, no runtime validation.
- **Pydantic models** for anything crossing a trust boundary (API request/response, config, identity documents). Runtime validation is the feature.

```python
# Internal: dataclass
@dataclass(frozen=True, slots=True)
class NeuronCandidate:
    persona_id: int
    content: str
    source_event_ids: tuple[int, ...]
    embedder_rev: str

# Trust boundary: Pydantic
class IngestRequest(BaseModel):
    persona_slug: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    signature: str = Field(pattern=r"^[A-Za-z0-9+/=]+$")
```

### Time

Timezone-aware only. UTC internally, local time only at the display boundary:

```python
from datetime import datetime, UTC

now = datetime.now(tz=UTC)        # good
now = datetime.utcnow()           # BANNED — deprecated, not tz-aware
now = datetime.now()              # BANNED — tz-naive
```

SQLite: store as ISO-8601 strings. Query with `datetime('now')` for DB-side default.
Postgres: `TIMESTAMPTZ`, never `TIMESTAMP`.

### IDs

`int` primary keys (SQLite autoincrement, Postgres `BIGSERIAL`). Not UUIDs.

Rationale: human-readable in logs, easy to reference in bug reports, trivially compact in indexes. The argument for UUIDs is distributed generation — which we don't have, since CLAUDE.md principle 7 says single-writer-per-table.

### Errors

Custom exception hierarchy rooted at `MemoryEngineError`:

```python
class MemoryEngineError(Exception):
    """Root of all memory_engine exceptions. Never raise this directly."""

class InvariantViolation(MemoryEngineError):
    """A governance invariant was violated. System should halt on critical."""

class GroundingRejection(MemoryEngineError):
    """Candidate neuron rejected by the grounding gate."""

class ScopeViolation(InvariantViolation):
    """Scope mismatch detected. Always critical."""
```

Never catch `Exception` at the top of a function. Catch specifically. If you genuinely need "anything that went wrong," catch `MemoryEngineError` — that means "our error domain."

### Logging

Per-module logger: `logger = logging.getLogger(__name__)`. No `print`. No `logging.info(...)` — always through the module logger.

Structured logs only. Use the project's `log()` helper:

```python
from memory_engine.observability.logging import log

log(
    event="grounding_verdict",
    persona_id=persona_id,
    candidate_hash=candidate.content_hash,
    verdict="accepted",
    similarity=0.73,
)
```

Required fields on every log: `ts` (added by formatter), `level`, `module`, `event`. Everything else is event-specific. See CLAUDE.md §1.7.

### Constants and config

Module-level constants in `UPPER_SNAKE`. Runtime configuration in `memory_engine.config`, not scattered. Environment variables read in one place (config.py); everything else imports from config.

```python
# memory_engine/config.py
class Settings(BaseSettings):
    db_url: str = Field(alias="MEMORY_ENGINE_DB_URL")
    vault_key: SecretStr = Field(alias="MEMORY_ENGINE_VAULT_KEY")
    monthly_budget_usd: float = Field(default=0.0)
    # ...

settings = Settings()

# Everywhere else
from memory_engine.config import settings
```

### Docstrings

Google style. Brief for internal helpers, thorough for public API.

```python
def append_event(
    *,
    persona_id: int,
    counterparty_id: int | None,
    event_type: str,
    scope: Scope,
    payload: dict[str, Any],
    signature: str,
    idempotency_key: str,
) -> Event:
    """Append an event to the immutable log.

    Verifies the signature before writing. Rejects duplicates by idempotency_key.
    Emits a structured log entry. Does not trigger consolidation; the
    background consolidator picks up new events asynchronously.

    Args:
        persona_id: Target persona. Must exist.
        counterparty_id: Optional counterparty. Required for counterparty_fact later.
        event_type: One of 'message_in', 'message_out', 'retrieval_trace', ...
        scope: Privacy classification — private, shared, or public.
        payload: Event body. JSON-serializable.
        signature: Ed25519 signature of (persona_id, content_hash). Base64.
        idempotency_key: Unique per source. Prevents double-ingest.

    Returns:
        The persisted Event with assigned id and recorded_at.

    Raises:
        SignatureInvalid: If the signature does not verify against the
            persona's registered MCP public key.
        IdempotencyConflict: If an event with this key already exists.
    """
```

## SQL

### Style

- `snake_case` for tables and columns.
- Plural for table names (`events`, `neurons`), singular for columns.
- Every table has `id INTEGER PRIMARY KEY` and one of `created_at` / `recorded_at`.
- Foreign keys are explicit with `REFERENCES`. Never rely on convention.
- Indexes named `ix_{table}_{columns}`. Partial indexes have a suffix describing the predicate.
- CHECK constraints encode invariants at the DB layer wherever possible.

### Parameterization

Always parameterized. Never f-string into SQL:

```python
# BANNED — SQL injection risk
cursor.execute(f"SELECT * FROM neurons WHERE persona_id = {persona_id}")

# Required
cursor.execute(
    "SELECT * FROM neurons WHERE persona_id = ?",
    (persona_id,),
)
```

Ruff rule `S608` enforces this. Do not suppress.

### Transactions

Every multi-statement write is in a transaction. For SQLite, that's explicit `BEGIN` / `COMMIT`:

```python
async with db.transaction() as tx:
    await tx.execute("INSERT INTO events ...", ...)
    await tx.execute("INSERT INTO working_memory ...", ...)
# commits on context exit; rolls back on exception
```

### Raw SQL, no ORM

CLAUDE.md commits to raw SQL. Rationale:

- Invariants live in CHECK constraints, foreign keys, and partial indexes — the DB enforces them. ORMs add a layer of Python validation that can be bypassed, but the DB can't.
- SQL is the contract. When a human or AI reviews a migration, the exact schema is visible. ORMs obscure it.
- Query performance is easier to reason about. `EXPLAIN QUERY PLAN` shows what the DB will do; you tune SQL, not ORM hints.

SQLAlchemy (even Core) is forbidden in `src/memory_engine`. Exception: tests may use helpers that reduce boilerplate, but those helpers must call parameterized SQL underneath.

## File layout

- One class per file when the class is substantial (>100 lines). Otherwise group related classes.
- `__init__.py` is small. It may export public API with `__all__`, but no logic.
- Tests mirror source structure: `tests/unit/core/test_events.py` tests `src/memory_engine/core/events.py`.

## Naming

- Functions: verbs. `append_event`, `verify_signature`, `run_grounding_gate`.
- Classes: nouns. `NeuronCandidate`, `GroundingVerdict`, `IdentityDocument`.
- Booleans: `is_X`, `has_X`, `should_X`. Never `flag_X`.
- Constants: `UPPER_SNAKE`.
- Private: `_leading_underscore`.
- Modules: `lowercase_with_underscores`, short. `events.py`, not `event_management_and_logging.py`.

## Forbidden

- Monkey-patching (except in tests via `monkeypatch` fixture).
- Globals except the module logger and the registered-invariants list.
- Catching and swallowing exceptions silently.
- `TODO` comments without a GitHub issue reference.
- Dead code commented out. Git has history; delete it.
- Hand-rolled crypto. Use pynacl or the `cryptography` library; never implement AES/RSA/HMAC yourself.
- Logging secrets. Ever. CI scans for patterns; use `SecretStr` for any field that should not print.
- Assert-based production checks. `assert` is disabled in optimized runs. Use explicit `if` + `raise`.
- `eval`, `exec`, `pickle` of untrusted data. No exceptions.
