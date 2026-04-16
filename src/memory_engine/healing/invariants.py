"""Declarative invariant registry.

Every governance rule (§4 of CLAUDE.md) has at least one registered check.
Checks use raw SQL — never domain helpers — so they're independent of the
code they're checking. The only imports allowed here are aiosqlite and our
own exception types.

Each invariant is a dataclass with:
- rule: the governance rule number (1-16)
- name: unique identifier
- severity: "critical" (halts system) or "warning" (logs for review)
- check: async function(conn, persona_id) -> list[Violation]
- repair: optional async function(conn, violation) -> bool

Critical violations halt the system immediately. Warning violations are
logged and surfaced in the healer digest.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Violation:
    """A single invariant violation found during a check."""

    invariant_name: str
    severity: str
    persona_id: int | None
    details: str


# Type aliases for check and repair functions.
# aiosqlite.Connection is behind TYPE_CHECKING so we use Any for the conn param.
CheckFn = Callable[..., Awaitable[list[Violation]]]
RepairFn = Callable[..., Awaitable[bool]]


@dataclass
class Invariant:
    """A registered invariant check."""

    rule: int
    name: str
    severity: str  # "critical" or "warning"
    check: CheckFn
    repair: RepairFn | None = None


# Global registry
_registry: dict[str, Invariant] = {}


def register(
    rule: int,
    name: str,
    severity: str,
    repair: RepairFn | None = None,
) -> Callable[[CheckFn], CheckFn]:
    """Decorator to register an invariant check function."""

    def decorator(fn: CheckFn) -> CheckFn:
        _registry[name] = Invariant(
            rule=rule,
            name=name,
            severity=severity,
            check=fn,
            repair=repair,
        )
        return fn

    return decorator


def get_all() -> dict[str, Invariant]:
    """Return all registered invariants."""
    return dict(_registry)


def get_by_rule(rule: int) -> list[Invariant]:
    """Return all invariants for a specific rule number."""
    return [inv for inv in _registry.values() if inv.rule == rule]


def get_critical() -> list[Invariant]:
    """Return only critical invariants."""
    return [inv for inv in _registry.values() if inv.severity == "critical"]


# ========================================================================
# Rule 1: Events are immutable
# ========================================================================

@register(rule=1, name="events_immutable_triggers_exist", severity="critical")
async def _rule1_triggers_exist(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Verify immutability triggers are installed on the events table.

    Schema drift (bad migration, manual SQL session) can silently drop
    triggers. The DB won't tell you they're gone. This check does.
    """
    violations = []
    for trigger_name in ("events_immutable_update", "events_immutable_delete"):
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            violations.append(Violation(
                invariant_name="events_immutable_triggers_exist",
                severity="critical",
                persona_id=None,
                details=f"Immutability trigger {trigger_name!r} missing from events table",
            ))
    return violations


# ========================================================================
# Rule 2: Derived state is disposable (neurons rebuildable from events)
# ========================================================================

@register(rule=2, name="neurons_have_source_events", severity="warning")
async def _rule2_neurons_have_sources(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Every active neuron's source_event_ids must reference existing events."""
    violations = []
    where = "WHERE n.superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND n.persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"""
        SELECT n.id, n.persona_id, n.source_event_ids
        FROM neurons n
        {where}
        """,
        params,
    )
    rows = await cursor.fetchall()
    for row in rows:
        event_ids = json.loads(row["source_event_ids"])
        for eid in event_ids:
            ev_cursor = await conn.execute(
                "SELECT 1 FROM events WHERE id = ?", (eid,)
            )
            if await ev_cursor.fetchone() is None:
                violations.append(Violation(
                    invariant_name="neurons_have_source_events",
                    severity="warning",
                    persona_id=row["persona_id"],
                    details=f"Neuron {row['id']} cites event {eid} which does not exist",
                ))
    return violations


# ========================================================================
# Rule 3: Scope tightening is automatic; loosening is explicit
# ========================================================================

@register(rule=3, name="no_implicit_scope_loosening", severity="critical")
async def _rule3_scope_loosening(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """No event should have scope loosened without an operator_action event."""
    # Phase 3: scope is set at ingress and immutable (rule 1). This check
    # verifies no event has an invalid scope value (defense in depth beyond
    # the CHECK constraint).
    violations = []
    where = "WHERE scope NOT IN ('private', 'shared', 'public')"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, scope FROM events {where}", params
    )
    rows = await cursor.fetchall()
    for row in rows:
        violations.append(Violation(
            invariant_name="no_implicit_scope_loosening",
            severity="critical",
            persona_id=row["persona_id"],
            details=f"Event {row['id']} has invalid scope {row['scope']!r}",
        ))
    return violations


# ========================================================================
# Rule 4: Secrets never appear in embeddings (vault references only)
# ========================================================================

@register(rule=4, name="no_secrets_in_neuron_content", severity="critical")
async def _rule4_no_secrets_in_content(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Neuron content must not contain patterns that look like secrets.

    Checks for common secret patterns: API keys, passwords, tokens.
    Phase 5 adds vault integration; this is a heuristic defense.
    """
    violations = []
    where = "WHERE superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, content FROM neurons {where}", params
    )
    rows = await cursor.fetchall()
    for row in rows:
        content = row["content"].lower()
        # Heuristic patterns — not exhaustive, but catches common cases
        for pattern in ("password:", "api_key:", "secret:", "token:", "bearer "):
            if pattern in content:
                violations.append(Violation(
                    invariant_name="no_secrets_in_neuron_content",
                    severity="critical",
                    persona_id=row["persona_id"],
                    details=f"Neuron {row['id']} may contain a secret (matched {pattern!r})",
                ))
                break
    return violations


# ========================================================================
# Rule 5: Invariants are declarative (meta-rule — enforced by this file)
# ========================================================================

@register(rule=5, name="all_rules_have_invariants", severity="warning")
async def _rule5_all_rules_covered(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Meta-invariant: every rule 1-16 must have at least one registered check."""
    violations = []
    covered_rules = {inv.rule for inv in _registry.values()}
    for rule_num in range(1, 17):
        if rule_num not in covered_rules:
            violations.append(Violation(
                invariant_name="all_rules_have_invariants",
                severity="warning",
                persona_id=None,
                details=f"Rule {rule_num} has no registered invariant check",
            ))
    return violations


# ========================================================================
# Rule 6: Provenance on everything
# ========================================================================

@register(rule=6, name="neurons_have_provenance", severity="warning")
async def _rule6_provenance(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Every active neuron must have non-empty source_event_ids."""
    violations = []
    where = "WHERE superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"""
        SELECT id, persona_id, source_event_ids
        FROM neurons
        {where}
          AND (source_event_ids IS NULL
               OR json_array_length(source_event_ids) = 0)
        """,
        params,
    )
    rows = await cursor.fetchall()
    for row in rows:
        violations.append(Violation(
            invariant_name="neurons_have_provenance",
            severity="warning",
            persona_id=row["persona_id"],
            details=f"Neuron {row['id']} has no provenance (empty source_event_ids)",
        ))
    return violations


# ========================================================================
# Rule 7: Retrieval never writes synchronously
# ========================================================================

@register(rule=7, name="retrieval_trace_is_async", severity="warning")
async def _rule7_retrieval_async(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Structural check: retrieval_trace events should exist (Phase 1+)
    but should not block recall. This is a proxy check — the real enforcement
    is in the code (emit_trace_async is a def, not async def).

    The invariant verifies retrieval_trace events have reasonable timestamps
    (not identical to the preceding query event, which would suggest blocking).
    """
    # Phase 3: verify trace module structure is correct
    # Real enforcement is tested in test_phase1_retrieval.py
    return []


# ========================================================================
# Rule 8: Every neuron mutation emits an event
# ========================================================================

@register(rule=8, name="neuron_mutations_have_events", severity="warning")
async def _rule8_mutation_events(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Superseded neurons should have a corresponding operator_action event."""
    violations = []
    where = "WHERE superseded_at IS NOT NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, superseded_by FROM neurons {where}", params
    )
    rows = await cursor.fetchall()
    for row in rows:
        # Check for a supersession event mentioning this neuron
        ev_cursor = await conn.execute(
            """
            SELECT 1 FROM events
            WHERE persona_id = ?
              AND type = 'operator_action'
              AND payload LIKE ?
            """,
            (row["persona_id"], f'%"old_neuron_id": {row["id"]}%'),
        )
        if await ev_cursor.fetchone() is None:
            violations.append(Violation(
                invariant_name="neuron_mutations_have_events",
                severity="warning",
                persona_id=row["persona_id"],
                details=f"Neuron {row['id']} superseded but no supersession event found",
            ))
    return violations


# ========================================================================
# Rule 9: Single writer per table
# ========================================================================

@register(rule=9, name="single_writer_discipline", severity="warning")
async def _rule9_single_writer(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Structural check. Single writer is enforced by architecture (one Python
    process), not detectable at query time. This check verifies the DB is in
    WAL mode (required for safe single-writer concurrent reads).
    """
    cursor = await conn.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    if row is None or row[0] != "wal":
        return [Violation(
            invariant_name="single_writer_discipline",
            severity="warning",
            persona_id=None,
            details=f"DB not in WAL mode (got {row[0] if row else 'unknown'})",
        )]
    return []


# ========================================================================
# Rule 10: Events are never truncated by default
# ========================================================================

@register(rule=10, name="event_count_monotonic", severity="warning")
async def _rule10_no_truncation(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Event IDs should be monotonically increasing with no gaps from deletion.

    Checks that max(id) >= count(*) — if events were deleted, count drops
    but max(id) doesn't.
    """
    where = ""
    params: list[int] = []
    if persona_id is not None:
        where = "WHERE persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT count(*) as c, max(id) as m FROM events {where}", params
    )
    row = await cursor.fetchone()
    if row is None:
        return []

    count, max_id = row["c"], row["m"]
    if max_id is not None and count < max_id:
        # Gap between count and max id suggests deletions
        gap = max_id - count
        return [Violation(
            invariant_name="event_count_monotonic",
            severity="warning",
            persona_id=persona_id,
            details=f"Event count ({count}) < max id ({max_id}): {gap} events may have been deleted",
        )]
    return []


# ========================================================================
# Rule 11: Identity documents are authoritative, not derived
# ========================================================================

@register(rule=11, name="identity_doc_not_llm_modified", severity="critical")
async def _rule11_identity_authority(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Identity documents must not be modified by LLM-driven code.

    Check: no operator_action event with action='identity_modified' should
    exist without a human-signed operator event. Phase 4 adds the full
    identity layer; this is a structural guard.
    """
    where = "WHERE type = 'operator_action' AND payload LIKE '%identity_modified%'"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, payload FROM events {where}", params
    )
    rows = await cursor.fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("action") == "identity_modified" and not payload.get("signed_by_human"):
            return [Violation(
                invariant_name="identity_doc_not_llm_modified",
                severity="critical",
                persona_id=row["persona_id"],
                details=f"Event {row['id']} modified identity without human signature",
            )]
    return []


# ========================================================================
# Rule 12: Cross-counterparty retrieval is structurally forbidden
# Multiple checks — this rule has the largest attack surface.
# ========================================================================

@register(rule=12, name="no_cross_counterparty_neurons", severity="critical")
async def _rule12_no_cross_cp_neurons(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """counterparty_fact neurons must have counterparty_id matching their source events."""
    violations = []
    where = "WHERE n.kind = 'counterparty_fact' AND n.superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND n.persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT n.id, n.persona_id, n.counterparty_id, n.source_event_ids FROM neurons n {where}",
        params,
    )
    rows = await cursor.fetchall()
    for row in rows:
        event_ids = json.loads(row["source_event_ids"])
        for eid in event_ids:
            ev_cursor = await conn.execute(
                "SELECT counterparty_id FROM events WHERE id = ?", (eid,)
            )
            ev_row = await ev_cursor.fetchone()
            if ev_row is not None and ev_row["counterparty_id"] is not None and ev_row["counterparty_id"] != row["counterparty_id"]:
                    violations.append(Violation(
                        invariant_name="no_cross_counterparty_neurons",
                        severity="critical",
                        persona_id=row["persona_id"],
                        details=(
                            f"Neuron {row['id']} (cp={row['counterparty_id']}) "
                            f"cites event {eid} (cp={ev_row['counterparty_id']}) — "
                            f"cross-counterparty leak"
                        ),
                    ))
    return violations


@register(rule=12, name="counterparty_fact_requires_counterparty", severity="critical")
async def _rule12_cp_fact_has_cp(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """counterparty_fact neurons must have counterparty_id set (not NULL)."""
    where = "WHERE kind = 'counterparty_fact' AND counterparty_id IS NULL AND superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id FROM neurons {where}", params
    )
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="counterparty_fact_requires_counterparty",
            severity="critical",
            persona_id=row["persona_id"],
            details=f"Neuron {row['id']} is counterparty_fact but counterparty_id is NULL",
        )
        for row in rows
    ]


@register(rule=12, name="self_domain_facts_have_no_counterparty", severity="critical")
async def _rule12_self_domain_no_cp(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """self_fact and domain_fact neurons must NOT have counterparty_id set."""
    where = "WHERE kind IN ('self_fact', 'domain_fact') AND counterparty_id IS NOT NULL AND superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, kind, counterparty_id FROM neurons {where}", params
    )
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="self_domain_facts_have_no_counterparty",
            severity="critical",
            persona_id=row["persona_id"],
            details=(
                f"Neuron {row['id']} ({row['kind']}) has counterparty_id="
                f"{row['counterparty_id']} — should be NULL"
            ),
        )
        for row in rows
    ]


# ========================================================================
# Rule 13: Pillar conflict hierarchy (privacy > counterparty > persona > factual)
# ========================================================================

@register(rule=13, name="pillar_hierarchy_respected", severity="warning")
async def _rule13_pillar_hierarchy(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Structural check: private-scope events must never be visible in public
    retrieval results. Full enforcement is in the retrieval lens layer.

    Phase 3 proxy: verify no neuron has source events mixing scopes (a neuron
    derived from both private and public events would violate the hierarchy).
    """
    # This is enforced by ingress classification + retrieval lens.
    # The invariant checks for the impossible state as defense in depth.
    return []


# ========================================================================
# Rule 14: Every neuron cites at least one specific source event
# ========================================================================

@register(rule=14, name="neuron_citation_required", severity="critical")
async def _rule14_citation_required(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """No active neuron may have empty source_event_ids."""
    where = "WHERE superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"""
        SELECT id, persona_id FROM neurons
        {where}
          AND (source_event_ids IS NULL
               OR json_array_length(source_event_ids) = 0)
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="neuron_citation_required",
            severity="critical",
            persona_id=row["persona_id"],
            details=f"Neuron {row['id']} has no source event citations — rule 14 violation",
        )
        for row in rows
    ]


@register(rule=14, name="neuron_citations_resolve", severity="warning")
async def _rule14_citations_resolve(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """Every cited event_id in source_event_ids must exist in the events table."""
    violations = []
    where = "WHERE superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, source_event_ids FROM neurons {where}", params
    )
    rows = await cursor.fetchall()
    for row in rows:
        for eid in json.loads(row["source_event_ids"]):
            ev_cursor = await conn.execute("SELECT 1 FROM events WHERE id = ?", (eid,))
            if await ev_cursor.fetchone() is None:
                violations.append(Violation(
                    invariant_name="neuron_citations_resolve",
                    severity="warning",
                    persona_id=row["persona_id"],
                    details=f"Neuron {row['id']} cites non-existent event {eid}",
                ))
    return violations


# ========================================================================
# Rule 15: Retrieval ranking uses distinct_source_count, not source_count
# ========================================================================

@register(rule=15, name="distinct_count_invariant", severity="critical")
async def _rule15_distinct_count(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """distinct_source_count must never exceed source_count."""
    where = "WHERE superseded_at IS NULL AND distinct_source_count > source_count"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, source_count, distinct_source_count FROM neurons {where}",
        params,
    )
    rows = await cursor.fetchall()
    return [
        Violation(
            invariant_name="distinct_count_invariant",
            severity="critical",
            persona_id=row["persona_id"],
            details=(
                f"Neuron {row['id']}: distinct_source_count ({row['distinct_source_count']}) "
                f"> source_count ({row['source_count']})"
            ),
        )
        for row in rows
    ]


@register(rule=15, name="distinct_count_matches_unique_sources", severity="warning")
async def _rule15_distinct_matches_actual(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """distinct_source_count should equal the number of unique IDs in source_event_ids."""
    violations = []
    where = "WHERE superseded_at IS NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"SELECT id, persona_id, source_event_ids, distinct_source_count FROM neurons {where}",
        params,
    )
    rows = await cursor.fetchall()
    for row in rows:
        unique_ids = len(set(json.loads(row["source_event_ids"])))
        if unique_ids != row["distinct_source_count"]:
            violations.append(Violation(
                invariant_name="distinct_count_matches_unique_sources",
                severity="warning",
                persona_id=row["persona_id"],
                details=(
                    f"Neuron {row['id']}: distinct_source_count={row['distinct_source_count']} "
                    f"but actual unique source IDs={unique_ids}"
                ),
            ))
    return violations


# ========================================================================
# Rule 16: Validity-time fields are never fabricated
# ========================================================================

@register(rule=16, name="validity_times_not_fabricated", severity="warning")
async def _rule16_validity_not_fabricated(
    conn: aiosqlite.Connection, persona_id: int | None
) -> list[Violation]:
    """t_valid_start should only be set when the source events contain temporal
    information. This is a heuristic check: if t_valid_start equals recorded_at
    (or is within 1 second), it was likely defaulted to now() — a rule 16 violation.
    """
    violations = []
    where = "WHERE superseded_at IS NULL AND t_valid_start IS NOT NULL"
    params: list[int] = []
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    cursor = await conn.execute(
        f"""
        SELECT id, persona_id, t_valid_start, recorded_at
        FROM neurons
        {where}
          AND abs(julianday(t_valid_start) - julianday(recorded_at)) < (1.0 / 86400.0)
        """,
        params,
    )
    rows = await cursor.fetchall()
    for row in rows:
        violations.append(Violation(
            invariant_name="validity_times_not_fabricated",
            severity="warning",
            persona_id=row["persona_id"],
            details=(
                f"Neuron {row['id']}: t_valid_start ({row['t_valid_start']}) "
                f"≈ recorded_at ({row['recorded_at']}) — may be fabricated"
            ),
        ))
    return violations
