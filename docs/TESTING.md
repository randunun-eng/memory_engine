# Testing Strategy

> Every governance rule has at least one invariant test. When the test fails, the system is wrong, not the test.

## Philosophy

Tests in this repo serve three distinct purposes:

1. **Correctness tests** (`tests/unit/`, `tests/integration/`) — "does this function do what it says?"
2. **Invariant tests** (`tests/invariants/`) — "do the governance rules still hold?" These are the load-bearing tests.
3. **Eval tests** (`tests/eval/`) — "is recall quality acceptable?" These are expected to be noisy; trends matter more than single runs.

Unit and integration tests are the bulk. Invariant tests are the safety net. Eval tests are the quality gauge.

## Structure

```
tests/
├── __init__.py
├── conftest.py                          # Shared fixtures, pytest config
├── unit/                                # Pure functions, no I/O
│   ├── core/
│   ├── policy/
│   └── retrieval/
├── integration/                         # DB-touching, in-memory SQLite
│   ├── test_phase0.py                   # Named per phase
│   ├── test_phase1.py
│   └── ...
├── invariants/                          # Governance rules
│   ├── test_events_immutable.py         # Rule 1
│   ├── test_derived_state_disposable.py # Rule 2
│   ├── test_scope_tightening.py         # Rule 3
│   ├── ...
│   └── test_all_rules_have_invariants.py # Meta: every rule has a check
├── fixtures/
│   ├── __init__.py
│   ├── personas.py                      # Canonical test personas
│   ├── events.py                        # Seed event streams
│   └── neurons.py                       # Seed neuron sets
└── eval/
    ├── baseline_queries.yaml            # 100 queries with expected results
    ├── test_recall_baseline.py          # Phase 7+ quality metric
    └── test_extraction_baseline.py      # Phase 7+ quality metric
```

## Running tests

```bash
uv run pytest                            # full suite
uv run pytest tests/unit -v              # just unit
uv run pytest tests/integration -v       # just integration
uv run pytest tests/invariants -v        # just invariants
uv run pytest -k grounding               # by keyword match
uv run pytest tests/eval --eval          # eval tests, gated by flag
```

Eval tests are excluded from default runs. They require the real embedder and take minutes. Run them explicitly when measuring quality.

## Unit tests

- No I/O. No DB. No file system. No network.
- Each test runs in milliseconds.
- Test one thing per test. Name describes behavior, not function.

```python
# Good
def test_content_hash_is_stable_across_calls() -> None:
    content = {"text": "hello", "timestamp": "2026-04-16"}
    assert compute_content_hash(content) == compute_content_hash(content)

def test_content_hash_differs_for_different_content() -> None:
    assert compute_content_hash({"x": 1}) != compute_content_hash({"x": 2})

# Bad
def test_content_hash() -> None:
    # Tests too many things, name is uninformative
    ...
```

## Integration tests

- Use SQLite `:memory:` for speed.
- Apply all migrations at setup.
- Seed just enough data for the test; use fixtures.
- Each test starts with a clean DB (fixture tears down between).

```python
# tests/integration/test_phase0.py
import pytest
from memory_engine.db.migrations import apply_all
from memory_engine.core.events import append_event, get_event

@pytest.fixture
async def db(tmp_path):
    """Fresh in-memory SQLite with migrations applied."""
    db_path = tmp_path / "test.db"
    conn = await connect(str(db_path))
    await apply_all(conn)
    yield conn
    await conn.close()

async def test_event_round_trip(db, seed_persona) -> None:
    """Append an event, retrieve by id, verify hash is stable."""
    event = await append_event(
        db,
        persona_id=seed_persona.id,
        event_type="message_in",
        scope="private",
        payload={"text": "hello"},
        signature=sign_test(seed_persona, {"text": "hello"}),
        idempotency_key="test-1",
    )
    retrieved = await get_event(db, event.id)
    assert retrieved.content_hash == event.content_hash
    assert retrieved.payload == {"text": "hello"}
```

## Invariant tests

Named after the rule they enforce. Every rule in CLAUDE.md §4 has at least one.

```python
# tests/invariants/test_events_immutable.py
import pytest
from memory_engine.core.events import append_event
from memory_engine.db.exceptions import UpdateForbidden

async def test_event_update_is_forbidden(db, seed_event) -> None:
    """Rule 1: events are immutable."""
    with pytest.raises(UpdateForbidden):
        await db.execute(
            "UPDATE events SET payload = ? WHERE id = ?",
            ('{"tampered": true}', seed_event.id),
        )

async def test_event_delete_is_forbidden(db, seed_event) -> None:
    """Rule 1: events are immutable — deletion is as forbidden as update."""
    with pytest.raises(DeleteForbidden):
        await db.execute("DELETE FROM events WHERE id = ?", (seed_event.id,))
```

**Meta-invariant:** the last test file checks that every rule has a test:

```python
# tests/invariants/test_all_rules_have_invariants.py
GOVERNANCE_RULES = range(1, 17)  # rules 1 through 16

def test_every_rule_has_at_least_one_invariant_test() -> None:
    """Meta: if you add a rule to CLAUDE.md §4, you must also add a test."""
    registered = load_registered_invariants()
    for rule_number in GOVERNANCE_RULES:
        assert any(inv.rule == rule_number for inv in registered), (
            f"Rule {rule_number} has no invariant test. "
            f"Add one in tests/invariants/"
        )
```

## Property-based tests (hypothesis)

For invariants that should hold across arbitrary inputs, use `hypothesis`:

```python
from hypothesis import given, strategies as st

@given(
    content=st.dictionaries(
        keys=st.text(min_size=1),
        values=st.one_of(st.text(), st.integers(), st.booleans()),
    )
)
def test_content_hash_is_deterministic(content: dict) -> None:
    """For any content, hash is deterministic across calls."""
    assert compute_content_hash(content) == compute_content_hash(content)
```

Use hypothesis when the invariant space is large and hand-picked inputs might miss edge cases. Don't use it for simple positive-path assertions where it's overkill.

## Fixtures

Share fixtures at the highest appropriate level. Don't duplicate.

- `conftest.py` at `tests/` — fixtures used across all test directories.
- `conftest.py` at `tests/integration/` — fixtures specific to integration tests (DB, seed data).
- `tests/fixtures/*.py` — data factories (canonical personas, event streams).

```python
# tests/fixtures/personas.py
from dataclasses import dataclass

@dataclass
class SeedPersona:
    id: int
    slug: str
    public_key: bytes
    private_key: bytes  # for tests only; production never exposes private keys

def make_test_persona(slug: str = "test_twin") -> SeedPersona:
    # Generates Ed25519 keypair, returns ready-to-use seed
    ...
```

## Coverage targets

Not a line-coverage target; a rule-coverage target.

- Every governance rule: covered by at least one invariant test.
- Every public API function: covered by at least one unit or integration test.
- Every migration: covered by a test that applies it clean and verifies post-conditions.

Line coverage as a metric is secondary. Aim for 80%+ because it's a useful proxy, but don't game it. A 100%-covered module that violates an invariant is worse than 60%-covered correct code.

## Flaky tests

Zero tolerance. A flaky test is either:
- A bug in the code (race condition, hidden dependency) — fix it.
- A bug in the test (timing assumption, order dependency) — fix it.
- Genuinely impossible to make deterministic — delete it and replace with an invariant check.

Never add `@pytest.mark.flaky` or retry-until-passing decorators. The next failure hides in the noise.

## CI expectations

`.github/workflows/test.yml` runs:
- `uv sync`
- `uv run ruff check`
- `uv run mypy src/`
- `uv run pytest tests/unit tests/integration tests/invariants -v --cov=memory_engine`

Green on every PR. Failing CI blocks merge. No exceptions.

## What we explicitly don't test

- Prometheus metric values (brittle, changes with version).
- Exact log output (structure yes, content no).
- LLM call results (mocked at the dispatch layer; real LLM calls only in `tests/eval/`).
- External service responses (mocked at the client layer).

Tests assert behavior, not implementation detail. If a refactor breaks a test but the behavior is unchanged, the test is wrong.
