"""Neuron factory for test fixtures.

Creates neurons directly in the DB (bypassing the consolidator, which
doesn't exist until Phase 2). Each neuron must have at least one
source event for citation integrity (rule 14).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


async def insert_neuron(
    conn: aiosqlite.Connection,
    *,
    neuron_id: int,
    persona_id: int,
    counterparty_id: int | None,
    kind: str,
    content: str,
    tier: str = "episodic",
    source_event_ids: list[int],
    distinct_source_count: int = 1,
    embedder_rev: str = "sbert-minilm-l6-v2-1",
    t_valid_start: str | None = None,
    t_valid_end: str | None = None,
    recorded_at: str = "2025-01-01 00:00:00",
    superseded_at: str | None = None,
    superseded_by: int | None = None,
) -> int:
    """Insert a neuron with a specific ID. Returns the neuron ID.

    Uses INSERT with explicit ID so fixture neurons have predictable IDs
    matching the phase1_baseline.yaml contract.
    """
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    source_json = json.dumps(source_event_ids)

    await conn.execute(
        """
        INSERT INTO neurons
            (id, persona_id, counterparty_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count, tier,
             t_valid_start, t_valid_end, recorded_at, superseded_at,
             superseded_by, embedder_rev)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            neuron_id,
            persona_id,
            counterparty_id,
            kind,
            content,
            content_hash,
            source_json,
            len(source_event_ids),
            distinct_source_count,
            tier,
            t_valid_start,
            t_valid_end,
            recorded_at,
            superseded_at,
            superseded_by,
            embedder_rev,
        ),
    )
    return neuron_id


async def insert_neuron_vec(
    conn: aiosqlite.Connection,
    neuron_id: int,
    embedding: list[float],
) -> None:
    """Insert a vector embedding for a neuron into neurons_vec."""
    embedding_json = json.dumps(embedding)
    await conn.execute(
        "INSERT INTO neurons_vec (neuron_id, embedding) VALUES (?, ?)",
        (neuron_id, embedding_json),
    )
