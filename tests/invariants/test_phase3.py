"""Phase 3 invariant tests — meta-tests and synthetic violation injection.

These tests verify the invariant system itself:
- Every rule 1-16 has at least one registered check (meta-test)
- At least 3 rules have multiple checks covering different attack vectors
- Synthetic violations are detected in a single scan
- Trigger existence is verified (rule 1 defense against schema drift)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.healing.checker import InvariantChecker
from memory_engine.healing.halt import get_halt_state
from memory_engine.healing.invariants import get_all, get_by_rule, get_critical
from memory_engine.policy.signing import canonical_signing_message, sign
from tests.fixtures.personas import make_test_persona

# ---- Helpers ----


async def _append_test_event(
    db: aiosqlite.Connection,
    persona_id: int,
    private_key: bytes,
    public_key_b64: str,
    *,
    counterparty_id: int | None = None,
    text: str = "test",
) -> int:
    payload = {"text": text}
    content_hash = compute_content_hash(payload)
    msg = canonical_signing_message(persona_id, content_hash)
    sig = sign(private_key, msg)
    event = await append_event(
        db,
        persona_id=persona_id,
        counterparty_id=counterparty_id,
        event_type="message_in",
        scope="private",
        payload=payload,
        signature=sig,
        public_key_b64=public_key_b64,
    )
    return event.id


# ---- Meta-test: all 16 rules have invariants ----


def test_all_16_rules_have_invariants() -> None:
    """Every governance rule (1-16) must have at least one registered check."""
    all_invariants = get_all()
    covered_rules = {inv.rule for inv in all_invariants.values()}

    for rule_num in range(1, 17):
        assert rule_num in covered_rules, (
            f"Rule {rule_num} has no registered invariant check"
        )


def test_at_least_3_rules_have_multiple_checks() -> None:
    """Rules with large attack surface must have multiple checks.

    Per Phase 3 guidance: rules 12, 14, 15 each need multiple checks.
    """
    multi_check_rules = []
    for rule_num in range(1, 17):
        checks = get_by_rule(rule_num)
        if len(checks) >= 2:
            multi_check_rules.append(rule_num)

    assert len(multi_check_rules) >= 3, (
        f"Only {len(multi_check_rules)} rules have multiple checks: {multi_check_rules}. "
        f"Expected at least 3 (rules 12, 14, 15)."
    )

    # Specifically verify the high-attack-surface rules
    assert len(get_by_rule(12)) >= 3, "Rule 12 (cross-counterparty) needs 3+ checks"
    assert len(get_by_rule(14)) >= 2, "Rule 14 (citations) needs 2+ checks"
    assert len(get_by_rule(15)) >= 2, "Rule 15 (distinct count) needs 2+ checks"


def test_critical_invariants_exist() -> None:
    """At least rules 1, 3, 4, 11, 12, 14, 15 have critical checks."""
    critical = get_critical()
    critical_rules = {inv.rule for inv in critical}

    for rule in (1, 3, 4, 11, 12, 14, 15):
        assert rule in critical_rules, (
            f"Rule {rule} should have at least one critical invariant"
        )


# ---- Rule 1: trigger existence ----


async def test_rule1_triggers_installed(db) -> None:
    """Both immutability triggers exist on the events table."""
    invariants = get_by_rule(1)
    assert len(invariants) >= 1

    trigger_check = invariants[0]
    violations = await trigger_check.check(db, None)
    assert len(violations) == 0, f"Triggers should be installed: {violations}"


async def test_rule1_detects_missing_update_trigger(db) -> None:
    """Dropping events_immutable_update is detected."""
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_update")
    await db.commit()

    invariants = get_by_rule(1)
    violations = await invariants[0].check(db, None)
    assert len(violations) == 1
    assert "events_immutable_update" in violations[0].details


async def test_rule1_detects_missing_delete_trigger(db) -> None:
    """Dropping events_immutable_delete is detected."""
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_delete")
    await db.commit()

    invariants = get_by_rule(1)
    violations = await invariants[0].check(db, None)
    assert len(violations) == 1
    assert "events_immutable_delete" in violations[0].details


async def test_rule1_detects_both_triggers_missing(db) -> None:
    """Dropping both triggers produces two violations."""
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_update")
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_delete")
    await db.commit()

    invariants = get_by_rule(1)
    violations = await invariants[0].check(db, None)
    assert len(violations) == 2


# ---- Rule 12: cross-counterparty injection ----


async def test_rule12_synthetic_cross_counterparty_detected(db) -> None:
    """Inject a cross-counterparty neuron; the checker catches it in one scan."""
    persona = await make_test_persona(db)

    # Two counterparties
    await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref) VALUES (?, ?)",
        (persona.id, "whatsapp:+1111"),
    )
    await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref) VALUES (?, ?)",
        (persona.id, "whatsapp:+2222"),
    )
    await db.commit()

    cursor = await db.execute(
        "SELECT id FROM counterparties WHERE external_ref = 'whatsapp:+1111'"
    )
    cp1 = (await cursor.fetchone())["id"]
    cursor = await db.execute(
        "SELECT id FROM counterparties WHERE external_ref = 'whatsapp:+2222'"
    )
    cp2 = (await cursor.fetchone())["id"]

    # Event from cp1
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64,
        counterparty_id=cp1, text="Alice's private info",
    )

    # Neuron assigned to cp2, citing cp1's event — the violation
    await db.execute(
        """
        INSERT INTO neurons
            (persona_id, counterparty_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, embedder_rev)
        VALUES (?, ?, 'counterparty_fact', 'leaked info', 'hash',
                ?, 1, 1, 'episodic', 'test-rev-1')
        """,
        (persona.id, cp2, json.dumps([event_id])),
    )
    await db.commit()

    state = get_halt_state()
    state.clear()

    checker = InvariantChecker(db, persona_id=persona.id)
    violations = await checker.run_scan()

    cross_cp = [v for v in violations if v.invariant_name == "no_cross_counterparty_neurons"]
    assert len(cross_cp) >= 1
    assert cross_cp[0].severity == "critical"
    assert state.is_halted

    state.clear()


# ---- Rule 14: citation checks ----


async def test_rule14_no_citation_detected(db) -> None:
    """A neuron with empty source_event_ids triggers rule 14."""
    persona = await make_test_persona(db)
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64,
    )

    # Insert valid neuron, then corrupt to empty citations (bypass CHECK)
    cursor = await db.execute(
        """
        INSERT INTO neurons
            (persona_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, embedder_rev)
        VALUES (?, 'self_fact', 'orphan', 'hash', ?, 1, 1, 'episodic', 'test-rev-1')
        """,
        (persona.id, json.dumps([event_id])),
    )
    await db.commit()
    nid = cursor.lastrowid

    await db.execute("PRAGMA ignore_check_constraints = ON")
    await db.execute(
        "UPDATE neurons SET source_event_ids = '[]', source_count = 0, distinct_source_count = 0 WHERE id = ?",
        (nid,),
    )
    await db.commit()
    await db.execute("PRAGMA ignore_check_constraints = OFF")

    checks = get_by_rule(14)
    citation_required = next(c for c in checks if c.name == "neuron_citation_required")
    violations = await citation_required.check(db, persona.id)
    assert len(violations) >= 1


async def test_rule14_dangling_citation_detected(db) -> None:
    """A neuron citing a non-existent event triggers rule 14."""
    persona = await make_test_persona(db)

    await db.execute(
        """
        INSERT INTO neurons
            (persona_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, embedder_rev)
        VALUES (?, 'self_fact', 'dangling ref', 'hash', '[99999]', 1, 1, 'episodic', 'test-rev-1')
        """,
        (persona.id,),
    )
    await db.commit()

    checks = get_by_rule(14)
    resolves = next(c for c in checks if c.name == "neuron_citations_resolve")
    violations = await resolves.check(db, persona.id)
    assert len(violations) >= 1
    assert "99999" in violations[0].details


# ---- Rule 15: distinct count ----


async def test_rule15_distinct_exceeds_source_detected(db) -> None:
    """distinct_source_count > source_count is a critical violation."""
    persona = await make_test_persona(db)
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64,
    )

    # CHECK constraint prevents distinct > source, so bypass it to simulate corruption
    await db.execute("PRAGMA ignore_check_constraints = ON")
    await db.execute(
        """
        INSERT INTO neurons
            (persona_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, embedder_rev)
        VALUES (?, 'self_fact', 'bad count', 'hash', ?, 1, 5, 'episodic', 'test-rev-1')
        """,
        (persona.id, json.dumps([event_id])),
    )
    await db.commit()
    await db.execute("PRAGMA ignore_check_constraints = OFF")

    checks = get_by_rule(15)
    invariant_check = next(c for c in checks if c.name == "distinct_count_invariant")
    violations = await invariant_check.check(db, persona.id)
    assert len(violations) >= 1
    assert violations[0].severity == "critical"


# ---- Full scan on clean database ----


async def test_full_scan_on_valid_data_clean(db) -> None:
    """A well-formed database produces zero critical violations."""
    persona = await make_test_persona(db)
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64,
    )

    # Insert a valid neuron
    await db.execute(
        """
        INSERT INTO neurons
            (persona_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, embedder_rev)
        VALUES (?, 'self_fact', 'Valid fact', 'hash', ?, 1, 1, 'episodic', 'test-rev-1')
        """,
        (persona.id, json.dumps([event_id])),
    )
    await db.commit()

    state = get_halt_state()
    state.clear()

    checker = InvariantChecker(db, persona_id=persona.id)
    violations = await checker.run_scan()

    critical = [v for v in violations if v.severity == "critical"]
    assert len(critical) == 0, f"Unexpected critical violations on valid data: {critical}"
    assert not state.is_halted

    state.clear()
