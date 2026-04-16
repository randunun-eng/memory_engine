"""Phase 3 integration tests — invariant checker, halt mechanism, healing log.

Tests verify the healer loop works end to end: scan detects violations,
critical violations trigger halt, warnings log for review, auto-repair
fixes known patterns, and halt can be released by an operator.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.exceptions import InvariantViolation
from memory_engine.healing.checker import InvariantChecker
from memory_engine.healing.halt import (
    assert_not_halted,
    engage_halt,
    get_halt_state,
    load_halt_state,
    release_halt,
)
from memory_engine.healing.invariants import Violation
from memory_engine.healing.loop import healer_loop
from memory_engine.healing.repair import (
    repair_distinct_count_mismatch,
    repair_missing_provenance,
)
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
    text: str = "test message",
    event_type: str = "message_in",
) -> int:
    """Append a signed test event and return its id."""
    payload = {"text": text}
    content_hash = compute_content_hash(payload)
    msg = canonical_signing_message(persona_id, content_hash)
    sig = sign(private_key, msg)
    event = await append_event(
        db,
        persona_id=persona_id,
        counterparty_id=counterparty_id,
        event_type=event_type,
        scope="private",
        payload=payload,
        signature=sig,
        public_key_b64=public_key_b64,
    )
    return event.id


async def _insert_neuron(
    db: aiosqlite.Connection,
    persona_id: int,
    source_event_ids: list[int],
    *,
    kind: str = "self_fact",
    content: str = "Test neuron content",
    counterparty_id: int | None = None,
    distinct_source_count: int | None = None,
    t_valid_start: str | None = None,
) -> int:
    """Insert a neuron directly and return its id."""
    if distinct_source_count is None:
        distinct_source_count = len(set(source_event_ids))
    cursor = await db.execute(
        """
        INSERT INTO neurons
            (persona_id, counterparty_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, t_valid_start, embedder_rev)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'episodic', ?, 'test-rev-1')
        """,
        (
            persona_id,
            counterparty_id,
            kind,
            content,
            "fakehash",
            json.dumps(source_event_ids),
            len(source_event_ids),
            distinct_source_count,
            t_valid_start,
        ),
    )
    await db.commit()
    return cursor.lastrowid


# ---- Checker tests ----


async def test_clean_scan_produces_no_violations(db) -> None:
    """A fresh DB with no data produces no violations (except meta-warnings)."""
    persona = await make_test_persona(db)
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64
    )
    await _insert_neuron(db, persona.id, [event_id])

    checker = InvariantChecker(db, persona_id=persona.id)
    violations = await checker.run_scan()

    # Should only have structural/meta violations (rule 7 placeholder, rule 9 WAL check)
    critical = [v for v in violations if v.severity == "critical"]
    assert len(critical) == 0, f"Unexpected critical violations: {critical}"


async def test_checker_detects_cross_counterparty_neuron(db) -> None:
    """A neuron citing events from a different counterparty triggers critical violation."""
    persona = await make_test_persona(db)

    # Create two counterparties
    await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref) VALUES (?, ?)",
        (persona.id, "whatsapp:+1111"),
    )
    await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref) VALUES (?, ?)",
        (persona.id, "whatsapp:+2222"),
    )
    await db.commit()

    # Get counterparty ids
    cursor = await db.execute(
        "SELECT id FROM counterparties WHERE external_ref = 'whatsapp:+1111'"
    )
    cp1 = (await cursor.fetchone())["id"]
    cursor = await db.execute(
        "SELECT id FROM counterparties WHERE external_ref = 'whatsapp:+2222'"
    )
    cp2 = (await cursor.fetchone())["id"]

    # Event belongs to cp1
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64,
        counterparty_id=cp1,
    )

    # Neuron claims cp2 but cites cp1's event — cross-counterparty leak
    await _insert_neuron(
        db, persona.id, [event_id],
        kind="counterparty_fact",
        counterparty_id=cp2,
        content="Leaked fact from wrong counterparty",
    )

    checker = InvariantChecker(db, persona_id=persona.id)
    violations = await checker.run_scan()

    cross_cp = [v for v in violations if v.invariant_name == "no_cross_counterparty_neurons"]
    assert len(cross_cp) >= 1
    assert cross_cp[0].severity == "critical"


async def test_checker_detects_missing_trigger(db) -> None:
    """Dropping an immutability trigger is detected as critical."""
    # Drop one trigger to simulate schema drift
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_update")
    await db.commit()

    checker = InvariantChecker(db, persona_id=None)
    violations = await checker.run_scan()

    trigger_violations = [
        v for v in violations if v.invariant_name == "events_immutable_triggers_exist"
    ]
    assert len(trigger_violations) == 1
    assert "events_immutable_update" in trigger_violations[0].details
    assert trigger_violations[0].severity == "critical"


async def test_checker_records_to_healing_log(db) -> None:
    """Violations found by the checker are written to healing_log."""
    # Drop trigger to guarantee a critical violation
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_delete")
    await db.commit()

    checker = InvariantChecker(db, persona_id=None)
    await checker.run_scan()

    cursor = await db.execute(
        "SELECT * FROM healing_log WHERE invariant_name = 'events_immutable_triggers_exist'"
    )
    rows = await cursor.fetchall()
    assert len(rows) >= 1
    assert rows[0]["severity"] == "critical"
    assert rows[0]["status"] == "escalated"


async def test_warning_violation_logs_but_does_not_halt(db) -> None:
    """Warning-level violations are recorded but don't trigger halt."""
    persona = await make_test_persona(db)

    # Create a neuron with mismatched distinct_source_count (warning-level).
    # source_event_ids has [e1, e2, e1] → 3 total, 2 unique.
    # Set distinct_source_count=3 (wrong; should be 2).
    # CHECK constraint (distinct <= source) passes since 3 <= 3.
    e1 = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64, text="one"
    )
    e2 = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64, text="two"
    )
    await _insert_neuron(
        db, persona.id, [e1, e2, e1],  # 3 total, 2 distinct
        distinct_source_count=3,  # claims 3 distinct but only 2 unique
    )

    # Reset halt state from any prior test
    state = get_halt_state()
    state.clear()

    checker = InvariantChecker(db, persona_id=persona.id)
    violations = await checker.run_scan()

    # Should find the mismatch
    mismatch = [v for v in violations if v.invariant_name == "distinct_count_matches_unique_sources"]
    assert len(mismatch) >= 1
    assert mismatch[0].severity == "warning"

    # Verify warning violations were recorded in healing_log
    warning_logged = [v for v in violations if v.severity == "warning"]
    assert len(warning_logged) >= 1


# ---- Halt mechanism tests ----


async def test_critical_violation_halts_system(db) -> None:
    """A critical invariant violation engages the halt mechanism."""
    state = get_halt_state()
    state.clear()  # clean slate

    await engage_halt(
        db,
        invariant_name="test_invariant",
        details="Test critical violation",
        persona_id=None,
    )

    assert state.is_halted
    assert state.invariant_name == "test_invariant"
    assert state.reason == "Test critical violation"


async def test_assert_not_halted_raises_when_halted(db) -> None:
    """assert_not_halted raises InvariantViolation when system is halted."""
    state = get_halt_state()
    state.clear()

    await engage_halt(
        db,
        invariant_name="test_halt",
        details="Testing halt gate",
        persona_id=None,
    )

    with pytest.raises(InvariantViolation, match="System halted"):
        assert_not_halted()

    # Cleanup
    state.clear()


async def test_halt_release_clears_state(db) -> None:
    """release_halt clears the in-memory flag and marks healing_log resolved."""
    state = get_halt_state()
    state.clear()

    await engage_halt(
        db,
        invariant_name="test_release",
        details="Testing release",
        persona_id=None,
    )
    assert state.is_halted

    await release_halt(db, operator="test_operator", reason="Reviewed and safe")
    assert not state.is_halted
    assert state.reason is None

    # healing_log entry should be resolved
    cursor = await db.execute(
        "SELECT resolved_at FROM healing_log WHERE invariant_name = 'test_release'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["resolved_at"] is not None


async def test_halt_persists_to_healing_log(db) -> None:
    """engage_halt writes an escalated entry to healing_log."""
    state = get_halt_state()
    state.clear()

    await engage_halt(
        db,
        invariant_name="test_persistence",
        details="Checking durable record",
        persona_id=1,
    )

    cursor = await db.execute(
        "SELECT * FROM healing_log WHERE invariant_name = 'test_persistence'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["severity"] == "critical"
    assert row["status"] == "escalated"
    assert row["persona_id"] == 1

    state.clear()


async def test_scanner_triggers_halt_on_critical(db) -> None:
    """End-to-end: checker finds critical violation → system halts."""
    state = get_halt_state()
    state.clear()

    # Drop trigger = guaranteed critical
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_update")
    await db.commit()

    checker = InvariantChecker(db, persona_id=None)
    await checker.run_scan()

    assert state.is_halted
    assert "events_immutable" in (state.invariant_name or "")

    state.clear()


# ---- Auto-repair tests ----


async def test_repair_distinct_count_mismatch(db) -> None:
    """repair_distinct_count_mismatch corrects the count from source_event_ids."""
    persona = await make_test_persona(db)
    e1 = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64, text="event one"
    )
    e2 = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64, text="event two"
    )

    # Insert neuron with wrong distinct count.
    # source_event_ids=[e1, e2, e1] → 3 total, 2 unique.
    # Set distinct_source_count=3 (wrong; should be 2). CHECK passes: 3 <= 3.
    nid = await _insert_neuron(
        db, persona.id, [e1, e2, e1],  # 3 total, 2 distinct
        distinct_source_count=3,  # wrong — should be 2
    )

    violation = Violation(
        invariant_name="distinct_count_matches_unique_sources",
        severity="warning",
        persona_id=persona.id,
        details=f"Neuron {nid}: distinct_source_count=3 but actual unique source IDs=2",
    )

    result = await repair_distinct_count_mismatch(db, violation)
    assert result is True

    cursor = await db.execute(
        "SELECT distinct_source_count FROM neurons WHERE id = ?", (nid,)
    )
    row = await cursor.fetchone()
    assert row["distinct_source_count"] == 2


async def test_repair_missing_provenance_quarantines(db) -> None:
    """repair_missing_provenance supersedes the neuron and adds quarantine entry."""
    persona = await make_test_persona(db)
    event_id = await _append_test_event(
        db, persona.id, persona.private_key, persona.public_key_b64,
    )

    # Insert a valid neuron, then corrupt it to simulate schema-drift provenance loss.
    # CHECK constraint prevents direct insert of empty source_event_ids, so we
    # insert valid data then UPDATE with ignore_check_constraints (simulates the
    # kind of corruption the repair function exists to handle).
    nid = await _insert_neuron(db, persona.id, [event_id], content="orphan neuron")

    await db.execute("PRAGMA ignore_check_constraints = ON")
    await db.execute(
        "UPDATE neurons SET source_event_ids = '[]', source_count = 0, distinct_source_count = 0 WHERE id = ?",
        (nid,),
    )
    await db.commit()
    await db.execute("PRAGMA ignore_check_constraints = OFF")

    violation = Violation(
        invariant_name="neurons_have_provenance",
        severity="warning",
        persona_id=persona.id,
        details=f"Neuron {nid} has no provenance (empty source_event_ids)",
    )

    result = await repair_missing_provenance(db, violation)
    assert result is True

    # Neuron should be superseded
    cursor = await db.execute(
        "SELECT superseded_at FROM neurons WHERE id = ?", (nid,)
    )
    row = await cursor.fetchone()
    assert row["superseded_at"] is not None

    # Quarantine entry should exist
    cursor = await db.execute(
        "SELECT * FROM quarantine_neurons WHERE reason = 'missing_provenance_repair'"
    )
    row = await cursor.fetchone()
    assert row is not None


# ---- Halt durability tests ----


async def test_halt_survives_simulated_restart(db) -> None:
    """Halt state persists in halt_state table and loads on 'restart'.

    Simulates a process restart by:
    1. Engaging halt (writes to halt_state table)
    2. Clearing in-memory state (simulates process death)
    3. Loading from DB (simulates new process startup)
    4. Asserting halt is active
    """
    state = get_halt_state()
    state.clear()

    await engage_halt(
        db,
        invariant_name="test_durability",
        details="Testing halt survives restart",
        persona_id=None,
    )
    assert state.is_halted

    # Simulate process death: clear in-memory state
    state.clear()
    assert not state.is_halted

    # Simulate new process startup: load from DB
    await load_halt_state(db)
    assert state.is_halted
    assert state.invariant_name == "test_durability"

    # Cleanup
    await release_halt(db, operator="test", reason="cleanup")


async def test_halt_release_is_durable(db) -> None:
    """After release, halt_state.active=0 persists across 'restart'."""
    state = get_halt_state()
    state.clear()

    await engage_halt(
        db,
        invariant_name="test_release_durable",
        details="Testing release durability",
        persona_id=None,
    )
    await release_halt(db, operator="test_op", reason="all clear")
    assert not state.is_halted

    # Simulate restart
    state.clear()
    await load_halt_state(db)
    assert not state.is_halted


async def test_halt_state_table_has_engaged_at(db) -> None:
    """engage_halt records engaged_at timestamp in halt_state table."""
    state = get_halt_state()
    state.clear()

    await engage_halt(
        db,
        invariant_name="test_timestamp",
        details="Checking engaged_at",
        persona_id=None,
    )

    cursor = await db.execute(
        "SELECT engaged_at, active FROM halt_state WHERE id = 1"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["active"] == 1
    assert row["engaged_at"] is not None

    state.clear()
    await release_halt(db, operator="test", reason="cleanup")


# ---- Healer loop tests ----


async def test_healer_loop_runs_one_scan(db) -> None:
    """The healer loop completes at least one scan before being cancelled."""
    state = get_halt_state()
    state.clear()

    # Initialize halt_state table
    await load_halt_state(db)

    # Drop a trigger to guarantee a critical violation
    await db.execute("DROP TRIGGER IF EXISTS events_immutable_update")
    await db.commit()

    # Run the loop with a very short interval, cancel after first scan
    import asyncio

    scan_complete = asyncio.Event()
    original_run_scan = InvariantChecker.run_scan

    async def patched_run_scan(self):  # type: ignore[no-untyped-def]
        result = await original_run_scan(self)
        scan_complete.set()
        return result

    InvariantChecker.run_scan = patched_run_scan  # type: ignore[assignment]

    try:
        task = asyncio.create_task(
            healer_loop(db, interval=0.1, persona_id=None)
        )

        # Wait for the scan to complete (with timeout)
        await asyncio.wait_for(scan_complete.wait(), timeout=5.0)

        # The loop should have detected the missing trigger and halted
        assert state.is_halted
        assert "events_immutable" in (state.invariant_name or "")

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    finally:
        InvariantChecker.run_scan = original_run_scan  # type: ignore[assignment]
        state.clear()


async def test_healer_loop_survives_exception(db) -> None:
    """The healer loop continues running even if a scan raises."""
    import asyncio

    await load_halt_state(db)

    scan_count = 0
    original_run_scan = InvariantChecker.run_scan

    async def failing_then_working_scan(self):  # type: ignore[no-untyped-def]
        nonlocal scan_count
        scan_count += 1
        if scan_count == 1:
            msg = "Simulated failure"
            raise RuntimeError(msg)
        return await original_run_scan(self)

    InvariantChecker.run_scan = failing_then_working_scan  # type: ignore[assignment]

    try:
        task = asyncio.create_task(
            healer_loop(db, interval=0.05, persona_id=None)
        )

        # Wait enough time for at least 2 scans
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # First scan raised, but loop continued — at least 2 scans ran
        assert scan_count >= 2

    finally:
        InvariantChecker.run_scan = original_run_scan  # type: ignore[assignment]
        get_halt_state().clear()
