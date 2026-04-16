# Phase 3 — Invariants + Healer

> **Status:** Blocked on Phase 2.
>
> **Duration:** 2 weeks (half-time solo).
>
> **Acceptance criterion:** Every governance rule in CLAUDE.md §4 has a registered invariant. A critical violation transitions the engine to read-only and produces a `halted` event; release requires a CLI action with a reason. The healer runs every 60 seconds and completes a full scan in under 30 seconds on a 10k-event DB. Synapses populate from contradiction edges and co-occurrence.

---

## Goal

Invariants become a system, not a pile of tests. The healer reads the log periodically, runs declarative invariant checks, and responds to violations by severity: info and warning are logged; critical halts ingest and requires operator intervention. Synapses table gets its first real population — contradiction edges from Phase 2 and co-occurrence from retrieval traces.

Phase 3 is the "we can trust the system more than before" phase. Without it, Phase 2's guarantees are all Python-layer discipline. With Phase 3, the DB has triggers for what it can enforce, and a periodic process catches what Python missed.

---

## Prerequisites

- Phase 2 complete. Consolidator and grounding gate running.
- Retrieval traces accumulating.
- Candidate quarantine operating.

---

## Schema changes

Migration 003. See `docs/SCHEMA.md` → Migration 003. Summary:

- `healing_log` — detected violations, with severity, status, resolved_at.
- `synapses` — edges between neurons. Contradiction edges from Phase 2 are backfilled here.

---

## File manifest

### Invariants

- `src/memory_engine/healing/__init__.py`
- `src/memory_engine/healing/invariants.py` — declarative registry.
- `src/memory_engine/healing/registry.py` — the `@invariant(rule=N, severity=...)` decorator.
- `src/memory_engine/healing/runner.py` — periodic scan loop.
- `src/memory_engine/healing/halt.py` — halt state management.
- `src/memory_engine/healing/repair.py` — safe repair actions (limited set; human approval required for the rest).

### Synapses

- `src/memory_engine/core/synapses.py` — edge creation and pruning.
- `src/memory_engine/core/cooccurrence.py` — co-occurrence analysis on retrieval traces.

### CLI additions

- `src/memory_engine/cli/heal.py` — `memory-engine heal run-once`, `heal status`.
- `src/memory_engine/cli/halt.py` — `memory-engine halt status`, `halt release --reason "..."`, `halt force --reason "..."`.

### HTTP additions

- `src/memory_engine/http/middleware/halt_check.py` — reject writes if halted.

### Tests

- `tests/integration/test_phase3.py`
- `tests/invariants/test_phase3.py`

---

## Invariant registry

A declarative, decorator-based registry. Every rule has one or more checks; the meta-test asserts every rule has at least one.

```python
# src/memory_engine/healing/registry.py

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

import aiosqlite


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class InvariantDef:
    rule: int                          # 1 through 16, references CLAUDE.md §4
    name: str
    severity: Severity
    check: Callable[[aiosqlite.Connection], Awaitable[list["Violation"]]]
    description: str


@dataclass(frozen=True)
class Violation:
    invariant_name: str
    persona_id: int | None
    severity: Severity
    details: dict


_REGISTRY: list[InvariantDef] = []


def invariant(*, rule: int, name: str, severity: Severity, description: str):
    def decorator(fn: Callable[[aiosqlite.Connection], Awaitable[list[Violation]]]):
        _REGISTRY.append(InvariantDef(
            rule=rule,
            name=name,
            severity=severity,
            check=fn,
            description=description,
        ))
        return fn
    return decorator


def registered_invariants() -> list[InvariantDef]:
    return list(_REGISTRY)
```

### Invariant implementations

```python
# src/memory_engine/healing/invariants.py

@invariant(
    rule=14,
    name="every_neuron_cites_at_least_one_event",
    severity=Severity.CRITICAL,
    description="Rule 14: every neuron must cite ≥ 1 source event.",
)
async def _rule14(conn):
    cursor = await conn.execute("""
        SELECT id, persona_id
        FROM neurons
        WHERE superseded_at IS NULL
          AND (source_event_ids IS NULL
               OR json_array_length(source_event_ids) = 0)
    """)
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="every_neuron_cites_at_least_one_event",
            persona_id=row["persona_id"],
            severity=Severity.CRITICAL,
            details={"neuron_id": row["id"]},
        )
        for row in rows
    ]


@invariant(
    rule=15,
    name="distinct_source_count_le_source_count",
    severity=Severity.WARNING,
    description="Rule 15 corollary: distinct_source_count must be ≤ source_count.",
)
async def _rule15_ordering(conn):
    cursor = await conn.execute("""
        SELECT id, persona_id, source_count, distinct_source_count
        FROM neurons
        WHERE distinct_source_count > source_count
    """)
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="distinct_source_count_le_source_count",
            persona_id=row["persona_id"],
            severity=Severity.WARNING,
            details={"neuron_id": row["id"]},
        )
        for row in rows
    ]


@invariant(
    rule=16,
    name="validity_times_never_defaulted",
    severity=Severity.WARNING,
    description="Rule 16: t_valid_start never fabricated to match recorded_at.",
)
async def _rule16(conn):
    # Heuristic: t_valid_start exactly equal to recorded_at within 1 second is
    # almost certainly a default-to-now bug (the validity-time should have
    # been left NULL if the source event didn't assert a date).
    cursor = await conn.execute("""
        SELECT id, persona_id
        FROM neurons
        WHERE t_valid_start IS NOT NULL
          AND ABS(strftime('%s', t_valid_start) - strftime('%s', recorded_at)) < 1
    """)
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="validity_times_never_defaulted",
            persona_id=row["persona_id"],
            severity=Severity.WARNING,
            details={"neuron_id": row["id"]},
        )
        for row in rows
    ]


@invariant(
    rule=12,
    name="cross_counterparty_partition_active_index",
    severity=Severity.CRITICAL,
    description="Rule 12: counterparty_fact neurons have counterparty_id set.",
)
async def _rule12(conn):
    # The CHECK constraint enforces this at insert time; this is a defence-in-depth
    # scan in case somebody modifies a row directly (they shouldn't).
    cursor = await conn.execute("""
        SELECT id, persona_id
        FROM neurons
        WHERE kind = 'counterparty_fact' AND counterparty_id IS NULL
    """)
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="cross_counterparty_partition_active_index",
            persona_id=row["persona_id"],
            severity=Severity.CRITICAL,
            details={"neuron_id": row["id"]},
        )
        for row in rows
    ]
```

Add further invariants for rules 1 (trigger test — the runner queries a sentinel row it expects to be unchanged), 3 (scope values in set), 11 (active neurons only reference unsuperseded neurons), 13 (pillar hierarchy on outbound — Phase 4 activation).

**Meta:** `tests/invariants/test_all_rules_have_invariants.py` imports `registered_invariants()` and asserts each of rules 1–16 is represented. If you add a rule and forget a check, CI fails.

---

## Healer runner

```python
async def run_once(conn: aiosqlite.Connection) -> RunReport:
    """Execute every registered invariant once. Log violations. Halt on critical.

    Returns a summary of the run: counts by severity, duration, halt transition.
    """
    started_at = datetime.now(UTC)
    all_violations: list[Violation] = []

    for defn in registered_invariants():
        t0 = time.monotonic()
        try:
            violations = await defn.check(conn)
        except Exception as exc:
            logger.exception("invariant_check_failed", extra={"invariant": defn.name})
            violations = [Violation(
                invariant_name=defn.name,
                persona_id=None,
                severity=Severity.WARNING,
                details={"error": str(exc)},
            )]
        dur_ms = int((time.monotonic() - t0) * 1000)
        log(event="invariant_checked", invariant=defn.name, count=len(violations), duration_ms=dur_ms)
        all_violations.extend(violations)

    # Persist to healing_log
    for v in all_violations:
        await conn.execute("""
            INSERT INTO healing_log (persona_id, invariant_name, severity, status, details)
            VALUES (?, ?, ?, 'detected', ?)
        """, (v.persona_id, v.invariant_name, v.severity.value, json.dumps(v.details)))
    await conn.commit()

    # Halt on critical
    criticals = [v for v in all_violations if v.severity == Severity.CRITICAL]
    if criticals and not await is_halted(conn):
        await force_halt(conn, reason=f"{len(criticals)} critical invariant violations")

    return RunReport(
        started_at=started_at,
        duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
        total_violations=len(all_violations),
        by_severity={s.value: sum(1 for v in all_violations if v.severity == s) for s in Severity},
    )
```

### Periodic loop

Phase 3's scheduler: a simple `asyncio` task running `run_once` every 60 seconds. Phase 6 replaces with a proper scheduler.

```python
async def run_forever(conn: aiosqlite.Connection, interval_seconds: int = 60) -> None:
    while True:
        try:
            await run_once(conn)
        except Exception:
            logger.exception("healer_run_once_failed")
        await asyncio.sleep(interval_seconds)
```

The `memory-engine serve` command starts this task alongside the HTTP server.

---

## Halt state

```python
# src/memory_engine/healing/halt.py

async def is_halted(conn: aiosqlite.Connection) -> bool:
    cursor = await conn.execute("""
        SELECT count(*) AS c FROM events
        WHERE type = 'halted' AND NOT EXISTS (
            SELECT 1 FROM events e2
            WHERE e2.type = 'halt_released'
              AND e2.recorded_at > events.recorded_at
        )
    """)
    row = await cursor.fetchone()
    return row["c"] > 0


async def force_halt(conn: aiosqlite.Connection, reason: str) -> None:
    # Emit a halted event. No signature required; this is an internal event type.
    await conn.execute("""
        INSERT INTO events (persona_id, type, scope, content_hash, payload, signature)
        VALUES (0, 'halted', 'private', ?, ?, '')
    """, (
        hashlib.sha256(reason.encode()).hexdigest(),
        json.dumps({"reason": reason, "at": datetime.now(UTC).isoformat()}),
    ))
    await conn.commit()
    logger.critical("engine_halted", extra={"reason": reason})


async def release_halt(conn: aiosqlite.Connection, reason: str, operator: str) -> None:
    await conn.execute("""
        INSERT INTO events (persona_id, type, scope, content_hash, payload, signature)
        VALUES (0, 'halt_released', 'private', ?, ?, '')
    """, (
        hashlib.sha256(reason.encode()).hexdigest(),
        json.dumps({"reason": reason, "operator": operator, "at": datetime.now(UTC).isoformat()}),
    ))
    await conn.commit()
```

**Why events for halt state?** Same reason principle 1 says the log is authoritative: halt state is itself derived from the log. If the log says `halted` and then `halt_released`, we are not halted. Operators can audit halts by reading events.

### Middleware

```python
@app.middleware("http")
async def halt_middleware(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if await is_halted(get_db()):
            return Response(
                content=json.dumps({"error": "engine halted", "retry_after": 0}),
                status_code=503,
                headers={"Retry-After": "0", "Content-Type": "application/json"},
            )
    return await call_next(request)
```

Reads (`GET /v1/recall`) continue during halt. Writes return 503.

---

## Synapses population

Two sources in Phase 3:

### 1. Contradictions (backfilled from Phase 2)

Phase 2's contradiction judge produced supersession. Phase 3 records each as a synapse:

```python
async def record_contradiction_synapse(
    conn: aiosqlite.Connection,
    *,
    newer_neuron_id: int,
    older_neuron_id: int,
    persona_id: int,
) -> None:
    await conn.execute("""
        INSERT OR IGNORE INTO synapses
            (persona_id, source_neuron, target_neuron, relation, weight)
        VALUES (?, ?, ?, 'contradicts', 1.0)
    """, (persona_id, newer_neuron_id, older_neuron_id))
    await conn.commit()
```

Backfill: on Phase 3 deploy, run `memory-engine heal backfill-synapses` once. It walks `neurons` looking for supersession pairs and inserts contradiction edges.

### 2. Co-occurrence

Neurons that appear together in retrieval results get `related_to` edges. Strength proportional to co-occurrence frequency.

```python
async def refresh_cooccurrence(
    conn: aiosqlite.Connection,
    lookback_days: int = 30,
    min_cooccurrence: int = 3,
) -> int:
    """Compute co-occurrence from retrieval traces over lookback window.

    For each pair of neurons that appeared together in ≥ min_cooccurrence recalls,
    upsert a 'related_to' synapse with weight = normalized frequency.

    Returns count of synapses inserted or updated.
    """
```

Runs as part of the consolidator's periodic work, not the healer. It's expensive; limit to a 30-day window and run at most hourly.

---

## Tests

### Integration (tests/integration/test_phase3.py)

```
test_healer_runs_to_completion
test_healer_detects_seeded_violation
test_critical_violation_halts_ingest
test_halt_persists_across_restart        # halt is a logged event, so it survives
test_halt_release_requires_reason
test_reads_continue_during_halt
test_writes_rejected_with_503_during_halt
test_synapse_contradiction_backfill
test_synapse_cooccurrence_from_traces
test_healer_per_run_under_30s_on_10k_events
```

### Invariants (tests/invariants/test_phase3.py)

```
test_every_rule_has_at_least_one_invariant    # meta
test_registered_invariants_match_rule_set
test_halt_event_is_immutable
test_halt_release_without_halt_is_noop
test_healer_does_not_delete_from_events       # rule 1 discipline
```

---

## Out of scope for this phase

- Automated repair for most violations. Phase 3's repair module has two cases: `stale_working_memory_entry` (safe prune) and `orphan_neuron_vec_row` (safe delete). Everything else is detected-and-logged only; repair happens by operator action.
- Alerting on halt (Phase 6: alertmanager integration).
- Dashboard for healing trends (Phase 6).
- Identity-layer invariants (Phase 4 adds them).
- Outbound invariants — redaction, non-negotiables (Phase 4).

---

## Common pitfalls

**False-positive criticals flapping the halt.** If a critical invariant has a race condition (reads during a write), it can detect a transient violation and halt. Mitigation: every critical check runs inside a read transaction; violations must reproduce on a second check 100ms later before halting. See `healer/runner.py::_confirm_critical()`.

**Halt that can't be released.** If the halt-release CLI has a bug, you're stuck. Test the release path in CI; keep a documented "break glass" SQL command in `docs/runbooks/halt_release_emergency.md` (Phase 6).

**Healer holding write locks.** SQLite in WAL mode allows concurrent reads during writes, but a long write transaction from the healer can block ingest. Keep healer scans read-only where possible; batch writes to `healing_log` in small transactions.

**Synapse explosion.** Co-occurrence synapses grow O(n²) with retrieval volume. The `min_cooccurrence=3` threshold is a guess; measure. Cap total synapses per neuron (Phase 3 uses 100; Phase 7 may tune).

**Invariant name drift.** Invariant names are used in `healing_log.invariant_name` and in test assertions. Rename with care; an unprocessed violation referencing an old name becomes confusing. Prefer adding new invariants over renaming old ones.

**Check ordering.** Registered order is iteration order. Put fast checks first so the common case (no violations) terminates quickly. The registry decorator preserves import order.

---

## When Phase 3 closes

Tag: `git tag phase-3-complete`. Update `CLAUDE.md` §8.

Commit message: `feat(phase3): invariant registry, halt-on-critical, synapse population`.
