"""System halt on critical invariant violations.

When a critical invariant is violated, the system halts:
- A 'halted' event is appended to the event log (rule 1: events are truth)
- An in-memory flag prevents ingest and recall
- The system returns 503 until a human releases the halt

Halt release appends a 'halt_released' event. It never deletes the
'halted' event — rule 1 protects this.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.exceptions import InvariantViolation

logger = logging.getLogger(__name__)


class HaltState:
    """In-memory halt flag.

    Shared across the process. When halted, the FastAPI server returns 503
    on /v1/ingest and /v1/recall. The flag is authoritative for the running
    process; the healing_log table is the durable record.
    """

    def __init__(self) -> None:
        self._halted: bool = False
        self._reason: str | None = None
        self._invariant_name: str | None = None

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def invariant_name(self) -> str | None:
        return self._invariant_name

    def halt(self, invariant_name: str, reason: str) -> None:
        """Set the halt flag. Idempotent — multiple critical violations
        don't stack; the first one wins."""
        if self._halted:
            logger.warning(
                "Already halted for %s; additional violation: %s — %s",
                self._invariant_name,
                invariant_name,
                reason,
            )
            return
        self._halted = True
        self._invariant_name = invariant_name
        self._reason = reason
        logger.critical(
            "SYSTEM HALTED — invariant %s: %s", invariant_name, reason
        )

    def release(self) -> None:
        """Clear the halt flag. Called after human review."""
        self._halted = False
        self._reason = None
        self._invariant_name = None
        logger.info("Halt released by operator")


# Module-level singleton. The checker and the API layer share this.
_halt_state = HaltState()


def get_halt_state() -> HaltState:
    """Return the process-wide halt state."""
    return _halt_state


async def engage_halt(
    conn: aiosqlite.Connection,
    *,
    invariant_name: str,
    details: str,
    persona_id: int | None,
) -> None:
    """Engage the system halt.

    1. Set in-memory flag (immediate effect on request handling).
    2. Write to healing_log (durable record).
    3. Log at CRITICAL.

    Does NOT append a 'halted' event to the events table yet — that
    requires a signature, which means the operator or the healer must
    hold signing keys. Phase 5 adds this. For now, healing_log is the
    durable record.
    """
    state = get_halt_state()
    state.halt(invariant_name, details)

    await conn.execute(
        """
        INSERT INTO healing_log
            (persona_id, invariant_name, severity, status, details)
        VALUES (?, ?, 'critical', 'escalated', ?)
        """,
        (persona_id, invariant_name, details),
    )
    await conn.commit()


async def release_halt(
    conn: aiosqlite.Connection,
    *,
    operator: str,
    reason: str,
) -> None:
    """Release the system halt after human review.

    1. Clear in-memory flag.
    2. Mark unresolved critical entries in healing_log as resolved.
    3. Log the release.

    The operator field is for audit. Phase 5 will verify the operator's
    signature.
    """
    state = get_halt_state()
    if not state.is_halted:
        logger.info("release_halt called but system is not halted")
        return

    state.release()

    await conn.execute(
        """
        UPDATE healing_log
        SET resolved_at = datetime('now'),
            details = details || ' | released by ' || ? || ': ' || ?
        WHERE severity = 'critical'
          AND resolved_at IS NULL
        """,
        (operator, reason),
    )
    await conn.commit()
    logger.info("Halt released by %s: %s", operator, reason)


def assert_not_halted() -> None:
    """Raise InvariantViolation if the system is halted.

    Call this at the top of /v1/ingest and /v1/recall handlers.
    """
    state = get_halt_state()
    if state.is_halted:
        raise InvariantViolation(
            f"System halted: {state.invariant_name} — {state.reason}"
        )
