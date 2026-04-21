"""Invariant checker — runs registered checks and records results.

The checker is the healer's heartbeat. It runs periodically (default 60s),
executes every registered invariant, writes results to healing_log, and
triggers halt on critical violations.

Usage:
    checker = InvariantChecker(conn, persona_id=1)
    violations = await checker.run_scan()
    # violations is the list of all found violations; critical ones
    # have already triggered halt.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.healing.halt import engage_halt
from memory_engine.healing.invariants import Violation, get_all

logger = logging.getLogger(__name__)


class InvariantChecker:
    """Runs all registered invariant checks against the database."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        persona_id: int | None = None,
    ) -> None:
        self._conn = conn
        self._persona_id = persona_id

    async def run_scan(self) -> list[Violation]:
        """Execute every registered invariant check.

        Returns all violations found. Critical violations also trigger
        system halt via engage_halt(). Warning/info violations are logged
        to healing_log for operator review.

        The scan is non-transactional on purpose: each check runs its own
        queries. A check failing mid-scan doesn't invalidate prior results.
        """
        all_violations: list[Violation] = []
        invariants = get_all()
        scan_start = time.monotonic()

        for name, invariant in invariants.items():
            try:
                violations = await invariant.check(self._conn, self._persona_id)
            except Exception:
                logger.exception("Invariant check %s raised an exception", name)
                violations = [
                    Violation(
                        invariant_name=name,
                        severity="warning",
                        persona_id=self._persona_id,
                        details=f"Check {name} raised an exception during scan",
                    )
                ]

            for v in violations:
                all_violations.append(v)
                await self._record_violation(v)

                if v.severity == "critical":
                    await engage_halt(
                        self._conn,
                        invariant_name=v.invariant_name,
                        details=v.details,
                        persona_id=v.persona_id,
                    )

        elapsed = time.monotonic() - scan_start
        logger.info(
            "Invariant scan complete: %d checks, %d violations, %.2fs",
            len(invariants),
            len(all_violations),
            elapsed,
        )

        return all_violations

    async def _record_violation(self, v: Violation) -> None:
        """Write a violation to the healing_log table."""
        status = "escalated" if v.severity == "critical" else "detected"
        await self._conn.execute(
            """
            INSERT INTO healing_log
                (persona_id, invariant_name, severity, status, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (v.persona_id, v.invariant_name, v.severity, status, v.details),
        )
        await self._conn.commit()

    async def run_critical_only(self) -> list[Violation]:
        """Run only critical invariants. Faster for hot-path checks."""
        from memory_engine.healing.invariants import get_critical

        all_violations: list[Violation] = []
        for invariant in get_critical():
            try:
                violations = await invariant.check(self._conn, self._persona_id)
            except Exception:
                logger.exception("Critical invariant %s raised an exception", invariant.name)
                continue

            for v in violations:
                all_violations.append(v)
                await self._record_violation(v)
                if v.severity == "critical":
                    await engage_halt(
                        self._conn,
                        invariant_name=v.invariant_name,
                        details=v.details,
                        persona_id=v.persona_id,
                    )

        return all_violations
