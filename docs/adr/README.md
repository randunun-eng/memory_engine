# Architecture Decision Records

> Each significant architectural decision is captured as a numbered ADR. The blueprint documents in `docs/blueprint/` explain *what* the system is; ADRs explain *why specific choices were made* and what the alternatives were. When reconsidering a decision, start by finding its ADR.

## Index

| # | Title | Status | Date |
|---|---|---|---|
| 0001 | [Python 3.12 with uv](./0001-python-with-uv.md) | Accepted | 2026-04-16 |
| 0002 | [SQLite by default, Postgres as scale-up](./0002-sqlite-default-postgres-scaleup.md) | Accepted | 2026-04-16 |
| 0003 | [Raw SQL, no ORM](./0003-raw-sql-no-orm.md) | Accepted | 2026-04-16 |
| 0004 | [Single process, async everywhere](./0004-single-process-async.md) | Accepted | 2026-04-16 |
| 0005 | [Ed25519 for MCP signatures](./0005-ed25519-for-mcp.md) | Accepted | 2026-04-16 |
| 0006 | [sentence-transformers all-MiniLM-L6-v2 as default embedder](./0006-sentence-transformers-default-embedder.md) | Accepted | 2026-04-16 |

## When to add an ADR

Add an ADR when you're about to make a decision that:
- Is hard to reverse. Changing the default embedder after 100k neurons exist is expensive.
- Affects multiple modules. Signature scheme touches ingress, auth, and audit.
- Will surprise a new contributor. "Why SQLite over Postgres?" is a question you should answer once, in writing.
- Has been debated. Post-debate, the ADR records the resolution and the path not taken.

Do not add an ADR for:
- Style choices that ruff enforces.
- Naming conventions (`docs/CODING.md` handles these).
- Single-module internal decisions that have no cross-cutting impact.

## Template

```markdown
# <NNNN> — <Short Title>

## Status

Accepted | Superseded by <XXXX> | Deprecated

## Context

The conditions under which the decision was made. What problem? What constraints? What else was considered?

## Decision

The decision itself. One or two sentences. Imperative voice.

## Consequences

What becomes easier? What becomes harder? What future decisions does this constrain?

## Alternatives considered

- **<Alternative A>** — why it was not chosen.
- **<Alternative B>** — why it was not chosen.

## Revisit if

Specific conditions that would justify reopening this decision. Otherwise, the decision stands.
```

## Numbering

ADRs are numbered sequentially. Never renumber. Superseded ADRs stay in place; their status changes to "Superseded by XXXX" and the superseding ADR references them.
