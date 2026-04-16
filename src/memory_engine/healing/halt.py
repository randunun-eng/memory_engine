"""System halt on critical invariant violations.

When a critical invariant is violated, the system halts:
- A durable row in halt_state records the halt (survives restart)
- An in-memory flag prevents ingest and recall (immediate effect)
- healing_log records the escalation (audit trail)
- The system returns 503 until a human releases the halt

Halt release sets halt_state.active=0. It never deletes the row or any
events — rule 1 protects this. The halt_state table is a singleton
(id=1 enforced by CHECK constraint).

Durability model: halt_state table is the source of truth. On process
start, load_halt_state() reads the table and sets the in-memory flag.
If the process crashed while halted, the new process comes up halted.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.exceptions import InvariantViolation

logger = logging.getLogger(__name__)


class HaltState:
    """In-memory halt flag backed by the halt_state table.

    The in-memory flag is the hot path (checked on every request).
    The table is the durable truth (checked on startup).
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

    def set_halted(self, invariant_name: str, reason: str) -> None:
        """Set the in-memory halt flag."""
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

    def clear(self) -> None:
        """Clear the in-memory halt flag."""
        self._halted = False
        self._reason = None
        self._invariant_name = None
        logger.info("Halt state cleared")


# Module-level singleton. The checker and the API layer share this.
_halt_state = HaltState()


def get_halt_state() -> HaltState:
    """Return the process-wide halt state."""
    return _halt_state


async def load_halt_state(conn: aiosqlite.Connection) -> None:
    """Load halt state from the database on process startup.

    If the halt_state table shows active=1, set the in-memory flag.
    This ensures halt survives process restarts.
    """
    state = get_halt_state()

    # Ensure singleton row exists
    await conn.execute(
        """
        INSERT OR IGNORE INTO halt_state (id, active) VALUES (1, 0)
        """
    )
    await conn.commit()

    cursor = await conn.execute(
        "SELECT active, invariant_name, details FROM halt_state WHERE id = 1"
    )
    row = await cursor.fetchone()
    if row is not None and row["active"] == 1:
        state.set_halted(
            invariant_name=row["invariant_name"] or "unknown",
            reason=row["details"] or "halt state loaded from database",
        )
        logger.warning(
            "Loaded active halt from database: %s — %s",
            row["invariant_name"],
            row["details"],
        )


async def engage_halt(
    conn: aiosqlite.Connection,
    *,
    invariant_name: str,
    details: str,
    persona_id: int | None,
) -> None:
    """Engage the system halt.

    1. Write to halt_state table (durable, survives restart).
    2. Set in-memory flag (immediate effect on request handling).
    3. Write to healing_log (audit trail).
    """
    state = get_halt_state()

    # Durable: write to halt_state table
    await conn.execute(
        """
        INSERT INTO halt_state (id, active, invariant_name, details, engaged_at)
        VALUES (1, 1, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            active = 1,
            invariant_name = excluded.invariant_name,
            details = excluded.details,
            engaged_at = excluded.engaged_at,
            released_at = NULL
        """,
        (invariant_name, details),
    )

    # Audit trail: write to healing_log
    await conn.execute(
        """
        INSERT INTO healing_log
            (persona_id, invariant_name, severity, status, details)
        VALUES (?, ?, 'critical', 'escalated', ?)
        """,
        (persona_id, invariant_name, details),
    )
    await conn.commit()

    # In-memory: immediate effect
    state.set_halted(invariant_name, details)


async def release_halt(
    conn: aiosqlite.Connection,
    *,
    operator: str,
    reason: str,
) -> None:
    """Release the system halt after human review.

    1. Update halt_state table (durable).
    2. Clear in-memory flag.
    3. Mark unresolved critical entries in healing_log as resolved.
    """
    state = get_halt_state()
    if not state.is_halted:
        logger.info("release_halt called but system is not halted")
        return

    # Durable: update halt_state
    await conn.execute(
        """
        UPDATE halt_state
        SET active = 0, released_at = datetime('now')
        WHERE id = 1
        """,
    )

    # Audit trail: resolve healing_log entries
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

    # In-memory: clear
    state.clear()
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
