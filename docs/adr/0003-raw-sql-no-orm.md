# 0003 — Raw SQL, no ORM

## Status

Accepted — 2026-04-16

## Context

Python has mature ORM and query-builder options: SQLAlchemy (both ORM and Core), Tortoise, Peewee, encode/databases, SQLModel (SQLAlchemy + Pydantic). Any of them would reduce boilerplate.

This project has specific properties that shift the ORM/raw-SQL trade-off:

1. **Invariants live in the schema.** CHECK constraints, partial indexes, immutability triggers are how we enforce governance rules 1, 3, 11, 12, 14 at the DB layer. An ORM that hides these makes audit harder.
2. **Small surface area.** Phase 0–7 produces roughly 15 tables. An ORM's productivity win scales with table count; at our scale, the overhead of the ORM itself is larger than the boilerplate it saves.
3. **Two dialects to support.** SQLite and Postgres. ORMs nominally abstract over dialects, but we've found in practice that edge cases (vector column placement, partial indexes with expressions, JSON operators) leak through the abstraction. Managing the leak in raw SQL is more honest.
4. **Performance is legible in SQL.** `EXPLAIN QUERY PLAN` on a raw query is readable. The same on ORM-generated SQL often requires first reverse-engineering what the ORM emitted.
5. **The code is meant to be read by AI auditors and human contributors who may not know our chosen ORM.** Every Python dev can read parameterized SQL. Not every Python dev reads SQLAlchemy 2.0 async syntax fluently.

The mem0 audit in the synthesis document found a reinforcement bug (the "808 echo" — using `source_count` instead of `distinct_source_count` for ranking). The bug lived inside a data-access layer that abstracted over the underlying SQL. The audit would have been trivial if the code had been `UPDATE neurons SET distinct_source_count = distinct_source_count + 1 WHERE ...` — a reviewer sees the column name on the left-hand side and thinks about whether that's the right column. The audit was not trivial because the code was `neuron.reinforce(source_event)` with a Python method that called internal methods that eventually generated SQL. Each layer of abstraction made review harder.

That doesn't mean ORMs are wrong. It means they're wrong for this project.

## Decision

Raw SQL with `aiosqlite` (and later `asyncpg` for Postgres). Every query is parameterized — no f-string interpolation. Results are converted to dataclasses manually in small helper functions.

Query construction pattern:

```python
async def get_active_neurons_for_counterparty(
    conn: aiosqlite.Connection,
    persona_id: int,
    counterparty_id: int,
) -> list[Neuron]:
    cursor = await conn.execute(
        """
        SELECT id, persona_id, counterparty_id, kind, content, tier,
               t_valid_start, t_valid_end, recorded_at,
               distinct_source_count, embedder_rev
        FROM neurons
        WHERE persona_id = ?
          AND superseded_at IS NULL
          AND (counterparty_id = ? OR kind = 'domain_fact')
        """,
        (persona_id, counterparty_id),
    )
    return [_row_to_neuron(row) async for row in cursor]
```

SQLAlchemy, both ORM and Core, is forbidden in `src/memory_engine`. Tests may use ad-hoc helpers for fixture setup, but those helpers also use parameterized SQL.

Query-builder libraries like `pypika` are permitted only inside the healer for constructing invariant checks generically, and only when the generated SQL is logged alongside execution for audit.

## Consequences

**Easier:**
- SQL is the ground truth. A reviewer sees `UPDATE neurons SET source_count = source_count + 1` and evaluates whether that's right. No layer of abstraction.
- Performance tuning is direct. `EXPLAIN` output maps back to the query you wrote.
- Migrations are SQL files. The schema at any migration number is completely expressed in those files.
- Onboarding: any Python developer reads the code. No SQLAlchemy learning curve.
- CI scans (gitleaks, custom ruff rules for f-string-in-SQL) operate on concrete SQL, not generated SQL.

**Harder:**
- Boilerplate. Row-to-dataclass conversion is manual and repetitive. We accept this; the repetition is shallow, not deep.
- Type safety on query results is our responsibility. `mypy` enforces the dataclass shape, but the mapping from row columns to fields is by hand.
- Refactoring the schema requires grepping for affected queries. We've adopted the convention of putting related queries in the same module (`core/events.py` owns events queries; `retrieval/sql.py` owns retrieval queries) to make grep scope obvious.
- N+1 queries are easier to introduce, harder to detect. Code review and occasional `EXPLAIN`-based audits catch them.

**Future constraints:**
- If the schema grows to 50+ tables, the boilerplate cost may exceed the legibility benefit. At that point we revisit, probably with a thin query builder (pypika or similar) rather than a full ORM.
- Migrations that span many tables are tedious to write. We accept this; migration tedium pays back in predictability.

## Alternatives considered

- **SQLAlchemy 2.0 Core.** Strong candidate. Rejected because the Core API, while explicit, still generates SQL you read through an abstraction. For our scale, the abstraction cost exceeds the save.
- **SQLModel.** SQLAlchemy + Pydantic. Rejected for the same reasons as SQLAlchemy Core, plus the additional runtime overhead of Pydantic model instantiation on every row.
- **Tortoise ORM.** Async-first Python ORM. Rejected because it is less widely known than SQLAlchemy, meaning even the "audience of Python developers" argument weakens, and it offers the same abstraction downsides.
- **Thin query builder (pypika, pugsql).** Considered. Would be the natural evolution if we outgrow raw SQL. Currently over-engineered for our surface area.
- **Prisma (via Python bindings).** Rejected. Adds a heavy Node runtime dependency for its engine; non-starter for a single-VM deployment.

## Revisit if

- Schema exceeds 40 tables.
- Boilerplate becomes the dominant cost in a per-month time audit of the codebase.
- A critical SQL injection vulnerability emerges because of missed parameterization (would mean our discipline is failing; ORM adds a layer of protection but the real fix is tooling — better ruff rules, better audit).
- A contributor base gathers that is more fluent in SQLAlchemy than in raw SQL.
