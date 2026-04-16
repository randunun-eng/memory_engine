"""Healer background loop.

Runs a full invariant scan every `interval` seconds. Started as an
asyncio task during `memory-engine serve`. Survives individual check
failures — an exception in one check doesn't kill the loop.

The loop is the teeth of the invariant system. Without it, invariants
are a self-audit tool, not a safety net.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.healing.checker import InvariantChecker

logger = logging.getLogger(__name__)

# Default: scan every 60 seconds. The scan itself must complete in <30s
# (acceptance criterion). If scan_duration > interval, the next scan
# is skipped with a warning — overlapping scans are a bug.
DEFAULT_INTERVAL_SECONDS = 60


async def healer_loop(
    conn: aiosqlite.Connection,
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    persona_id: int | None = None,
) -> None:
    """Run invariant scans in an infinite loop.

    This function never returns under normal operation. It is designed
    to be launched with asyncio.create_task() during server startup.

    Args:
        conn: Database connection for the healer to use.
        interval: Seconds between scan starts.
        persona_id: If set, scans only this persona's invariants.
    """
    logger.info(
        "Healer loop starting: interval=%ds, persona_id=%s",
        interval,
        persona_id,
    )

    while True:
        scan_start = time.monotonic()

        try:
            checker = InvariantChecker(conn, persona_id=persona_id)
            violations = await checker.run_scan()

            scan_duration = time.monotonic() - scan_start
            logger.info(
                "Healer scan complete: %d violations in %.2fs",
                len(violations),
                scan_duration,
            )

            if scan_duration > interval:
                logger.warning(
                    "Healer scan took %.1fs, exceeding interval of %.1fs — "
                    "overlapping scans would be a bug. Consider increasing "
                    "interval or optimizing checks.",
                    scan_duration,
                    interval,
                )

        except Exception:
            # The loop must survive. Individual check failures are logged
            # by the checker; this catches anything that escapes.
            logger.exception("Healer scan raised an unexpected exception")

        # Sleep for the remainder of the interval (minus scan time).
        elapsed = time.monotonic() - scan_start
        sleep_time = max(0, interval - elapsed)
        await asyncio.sleep(sleep_time)


def start_healer_task(
    conn: aiosqlite.Connection,
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    persona_id: int | None = None,
) -> asyncio.Task[None]:
    """Create and return the healer background task.

    The caller is responsible for keeping a strong reference to the
    returned task. Module-level storage recommended:

        _healer_task = start_healer_task(conn)

    See src/memory_engine/retrieval/trace.py for the same pattern.
    """
    task = asyncio.create_task(
        healer_loop(conn, interval=interval, persona_id=persona_id),
        name="healer-loop",
    )
    logger.info("Healer background task created")
    return task
