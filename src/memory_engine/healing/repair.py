"""Auto-repair library for known invariant violation patterns.

Each repair function addresses a specific violation type. Repairs are
conservative: they log what they do and never delete events (rule 1).

Repairs are registered on the invariant they fix via the `repair` parameter
in the @register decorator. Not every invariant has a repair — critical
violations typically require human review, not auto-repair.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.healing.invariants import Violation

logger = logging.getLogger(__name__)


async def repair_missing_provenance(
    conn: aiosqlite.Connection,
    violation: Violation,
) -> bool:
    """Quarantine neurons with missing provenance (rule 6).

    Moves the neuron to quarantine by superseding it with a marker.
    The neuron is not deleted — it's superseded_at is set, and a
    quarantine_neurons row records the issue.
    """
    # Extract neuron id from the details string
    # Details format: "Neuron {id} has no provenance ..."
    parts = violation.details.split()
    if len(parts) < 2 or not parts[1].isdigit():
        logger.warning("Cannot parse neuron id from violation: %s", violation.details)
        return False

    neuron_id = int(parts[1])

    # Supersede the neuron (marks it inactive)
    await conn.execute(
        "UPDATE neurons SET superseded_at = datetime('now') WHERE id = ? AND superseded_at IS NULL",
        (neuron_id,),
    )

    # Record in quarantine
    cursor = await conn.execute(
        "SELECT content, source_event_ids FROM neurons WHERE id = ?",
        (neuron_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False

    await conn.execute(
        """
        INSERT INTO quarantine_neurons
            (persona_id, candidate_json, reason, source_event_ids)
        VALUES (?, ?, 'missing_provenance_repair', ?)
        """,
        (violation.persona_id, row["content"], row["source_event_ids"]),
    )
    await conn.commit()

    logger.info("Repaired: quarantined neuron %d (missing provenance)", neuron_id)
    return True


async def repair_distinct_count_mismatch(
    conn: aiosqlite.Connection,
    violation: Violation,
) -> bool:
    """Fix distinct_source_count to match actual unique source IDs (rule 15).

    This is a safe repair: recalculate from the source_event_ids JSON array.
    """
    parts = violation.details.split()
    if len(parts) < 2 or not parts[1].rstrip(":").isdigit():
        logger.warning("Cannot parse neuron id from violation: %s", violation.details)
        return False

    neuron_id = int(parts[1].rstrip(":"))

    cursor = await conn.execute(
        "SELECT source_event_ids FROM neurons WHERE id = ?",
        (neuron_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False

    unique_count = len(set(json.loads(row["source_event_ids"])))
    await conn.execute(
        "UPDATE neurons SET distinct_source_count = ? WHERE id = ?",
        (unique_count, neuron_id),
    )
    await conn.commit()

    logger.info(
        "Repaired: neuron %d distinct_source_count corrected to %d",
        neuron_id,
        unique_count,
    )
    return True
