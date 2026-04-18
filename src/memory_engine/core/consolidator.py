"""Consolidator: the background loop that turns events into neurons.

Four operations, in order:
1. Promote — new events enter working memory, candidates extracted + grounded
2. Reinforce — existing neurons cited again get distinct_source_count bumped
3. Decay — working memory activation decays over time
4. Prune — below-threshold working memory entries are dropped

The consolidator is a background task (pitfall 8 in CLAUDE.md §13). It never
runs on the main request path. Ingress ends at event append; consolidation
is eventually-consistent.

Rule 7: retrieval never writes synchronously — the consolidator is the writer.
Rule 8: every neuron mutation emits an event.
Rule 14: every neuron cites at least one specific source event.
Rule 15: ranking uses distinct_source_count, not source_count.
Rule 16: validity times never default to now.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.core.extraction import NeuronCandidate
    from memory_engine.policy.dispatch import PolicyDispatch

from memory_engine.core.contradiction import (
    check_contradiction,
    find_overlapping_neurons,
    supersede_neuron,
)
from memory_engine.core.events import Event, append_event, compute_content_hash, get_event
from memory_engine.core.extraction import extract_candidates
from memory_engine.core.grounding import (
    Verdict,
    grounding_gate,
    quarantine_candidate,
)
from memory_engine.policy.signing import canonical_signing_message, sign

logger = logging.getLogger(__name__)


async def consolidation_pass(
    conn: aiosqlite.Connection,
    dispatch: PolicyDispatch,
    persona_id: int,
    private_key: bytes,
    public_key_b64: str,
    *,
    embedder_rev: str = "sbert-minilm-l6-v2-1",
    embed_fn: Any | None = None,
    similarity_threshold: float = 0.40,
    decay_half_life_minutes: int = 30,
    activation_threshold: float = 0.1,
    working_memory_capacity: int = 64,
    max_events_per_pass: int | None = 16,
) -> ConsolidationStats:
    """Run one full consolidation pass for a persona.

    This is the top-level entry point. It runs promote, reinforce, decay,
    and prune in sequence.

    Args:
        conn: DB connection.
        dispatch: Policy dispatch for LLM calls.
        persona_id: Target persona.
        private_key: Ed25519 private key for signing events.
        public_key_b64: Corresponding public key.
        embedder_rev: Current embedder revision string.
        embed_fn: Callable(text) -> list[float] for grounding similarity.
        similarity_threshold: Grounding gate threshold.
        decay_half_life_minutes: Activation half-life for decay.
        activation_threshold: Minimum activation to keep in working memory.
        working_memory_capacity: Max working memory entries before forced prune.

    Returns:
        ConsolidationStats with counts of each operation.
    """
    stats = ConsolidationStats()

    # 1. Promote — ingest new events into working memory + extract candidates.
    # `max_events_per_pass` caps the prompt size / LLM latency per tick; the
    # next tick picks up the remainder. None means "no cap".
    new_events = await _find_unconsolidated_events(
        conn, persona_id, limit=max_events_per_pass,
    )
    for event in new_events:
        await _enter_working_memory(conn, persona_id, event.id)
        stats.events_entered += 1

    if new_events:
        extraction_result = await extract_candidates(
            dispatch,
            events=new_events,
            persona_id=persona_id,
            counterparty_id=_infer_counterparty(new_events),
        )

        for candidate in extraction_result.candidates:
            # Fetch source events for grounding
            source_events = []
            for eid in candidate.source_event_ids:
                evt = await get_event(conn, eid)
                if evt is not None:
                    source_events.append(evt)

            # Run grounding gate
            result = await grounding_gate(
                candidate,
                source_events,
                conn,
                persona_id,
                dispatch=dispatch,
                similarity_threshold=similarity_threshold,
                embed_fn=embed_fn,
            )

            if result.verdict == Verdict.ACCEPT:
                neuron_id = await _promote_candidate(
                    conn, candidate, persona_id, embedder_rev,
                    private_key, public_key_b64,
                    dispatch=dispatch,
                )
                if neuron_id is not None:
                    stats.neurons_promoted += 1
            else:
                await quarantine_candidate(
                    conn, candidate, persona_id,
                    reason=result.reason or "unknown",
                )
                stats.candidates_quarantined += 1

    # 2. Reinforce — bump distinct_source_count for neurons re-cited
    reinforced = await _reinforce_existing(conn, persona_id, new_events)
    stats.neurons_reinforced = reinforced

    # 3. Decay — reduce activation of working memory entries
    decayed = await _decay_working_memory(conn, persona_id, decay_half_life_minutes)
    stats.entries_decayed = decayed

    # 4. Prune — remove below-threshold entries
    pruned = await _prune_working_memory(
        conn, persona_id, activation_threshold, working_memory_capacity,
    )
    stats.entries_pruned = pruned

    logger.info(
        "Consolidation pass persona=%d: %d events, %d promoted, %d quarantined, "
        "%d reinforced, %d decayed, %d pruned",
        persona_id, stats.events_entered, stats.neurons_promoted,
        stats.candidates_quarantined, stats.neurons_reinforced,
        stats.entries_decayed, stats.entries_pruned,
    )

    return stats


class ConsolidationStats:
    """Counters for a single consolidation pass."""

    def __init__(self) -> None:
        self.events_entered: int = 0
        self.neurons_promoted: int = 0
        self.candidates_quarantined: int = 0
        self.neurons_reinforced: int = 0
        self.entries_decayed: int = 0
        self.entries_pruned: int = 0


# ---- Internal helpers ----


async def _find_unconsolidated_events(
    conn: aiosqlite.Connection,
    persona_id: int,
    *,
    limit: int | None = None,
) -> list[Event]:
    """Find events not yet entered into working memory.

    Only processes message_in and message_out events — retrieval_trace and
    operator_action events don't produce neurons.

    `limit` caps the batch size so a single extraction call doesn't see
    an unbounded event list (keeps prompt size and LLM latency predictable).
    """
    sql = """
        SELECT e.id, e.persona_id, e.counterparty_id, e.type, e.scope,
               e.content_hash, e.idempotency_key, e.payload, e.signature,
               e.recorded_at
        FROM events e
        LEFT JOIN working_memory wm ON wm.event_id = e.id AND wm.persona_id = e.persona_id
        WHERE e.persona_id = ?
          AND e.type IN ('message_in', 'message_out')
          AND wm.id IS NULL
        ORDER BY e.recorded_at ASC
    """
    params: tuple[Any, ...] = (persona_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (persona_id, limit)
    cursor = await conn.execute(sql, params)
    rows = await cursor.fetchall()
    return [
        Event(
            id=row["id"],
            persona_id=row["persona_id"],
            counterparty_id=row["counterparty_id"],
            type=row["type"],
            scope=row["scope"],
            content_hash=row["content_hash"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload"]),
            signature=row["signature"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]).replace(tzinfo=UTC),
        )
        for row in rows
    ]


async def _enter_working_memory(
    conn: aiosqlite.Connection,
    persona_id: int,
    event_id: int,
    activation: float = 1.0,
) -> None:
    """Add an event to the working memory ring buffer."""
    await conn.execute(
        "INSERT INTO working_memory (persona_id, event_id, activation) VALUES (?, ?, ?)",
        (persona_id, event_id, activation),
    )
    await conn.commit()


async def _promote_candidate(
    conn: aiosqlite.Connection,
    candidate: NeuronCandidate,
    persona_id: int,
    embedder_rev: str,
    private_key: bytes,
    public_key_b64: str,
    *,
    dispatch: PolicyDispatch | None = None,
) -> int | None:
    """Promote a grounded candidate to the neurons table.

    Checks for contradictions with existing neurons first. If a contradiction
    is found, the old neuron is superseded.

    Returns the new neuron's id, or None if promotion failed.
    """
    content_hash = _hash_content(candidate.content)
    source_event_ids_json = json.dumps(candidate.source_event_ids)
    counterparty_id = None

    # Resolve counterparty_id from events if this is a counterparty_fact
    if candidate.kind == "counterparty_fact":
        for eid in candidate.source_event_ids:
            event = await get_event(conn, eid)
            if event is not None and event.counterparty_id is not None:
                counterparty_id = event.counterparty_id
                break

    # Exact-content dedup: if an active neuron with the same content_hash
    # already exists for this (persona, kind, counterparty), reinforce it
    # instead of inserting a duplicate. The extractor is non-deterministic
    # and tick-driven re-processing (events re-surfacing after working_memory
    # decay) produces identical-text neurons that poison BM25's IDF — once
    # "harsha" is in 10/24 neurons, its IDF collapses to 0 and retrieval
    # returns nothing. See DRIFT `consolidator-duplicate-extraction-loop`.
    existing_cursor = await conn.execute(
        """
        SELECT id, source_event_ids, source_count, distinct_source_count
        FROM neurons
        WHERE persona_id = ? AND kind = ? AND content_hash = ?
          AND superseded_at IS NULL
          AND (counterparty_id IS ? OR counterparty_id = ?)
        LIMIT 1
        """,
        (persona_id, candidate.kind, content_hash, counterparty_id, counterparty_id),
    )
    existing_row = await existing_cursor.fetchone()
    if existing_row is not None:
        existing_ids = set(json.loads(existing_row["source_event_ids"]))
        new_ids = set(candidate.source_event_ids)
        truly_new = new_ids - existing_ids
        merged_ids = sorted(existing_ids | new_ids)
        new_source_count = existing_row["source_count"] + len(new_ids)
        new_distinct = existing_row["distinct_source_count"] + len(truly_new)
        await conn.execute(
            """
            UPDATE neurons
            SET source_count = ?,
                distinct_source_count = ?,
                source_event_ids = ?
            WHERE id = ?
            """,
            (new_source_count, new_distinct, json.dumps(merged_ids), existing_row["id"]),
        )
        await conn.commit()
        logger.info(
            "dedup: reinforced existing neuron %d (distinct=%d) instead of inserting duplicate",
            existing_row["id"], new_distinct,
        )
        return int(existing_row["id"])

    # Check for contradictions with existing neurons
    if dispatch is not None:
        overlapping = await find_overlapping_neurons(
            conn, persona_id, candidate.content, candidate.kind, counterparty_id,
        )
        for existing in overlapping:
            try:
                result = await check_contradiction(
                    dispatch,
                    persona_id=persona_id,
                    neuron_a_content=existing["content"],
                    neuron_b_content=candidate.content,
                    entity_key=_derive_entity_key(candidate.content, existing["content"]),
                )
                if result.relation == "contradict":
                    # New supersedes old (candidate is "b", the newer one)
                    # We'll supersede after inserting the new neuron below
                    pass
            except Exception:
                logger.warning(
                    "Contradiction check failed for neuron %d", existing["id"],
                    exc_info=True,
                )

    # Insert the neuron
    cursor = await conn.execute(
        """
        INSERT INTO neurons
            (persona_id, counterparty_id, kind, content, content_hash,
             source_event_ids, source_count, distinct_source_count,
             tier, t_valid_start, embedder_rev)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            persona_id,
            counterparty_id,
            candidate.kind,
            candidate.content,
            content_hash,
            source_event_ids_json,
            len(candidate.source_event_ids),
            len(set(candidate.source_event_ids)),
            candidate.target_tier,
            candidate.t_valid_start,  # Rule 16: None if not asserted
            embedder_rev,
        ),
    )
    await conn.commit()
    neuron_id = cursor.lastrowid
    assert neuron_id is not None

    # Now handle supersession for contradictions
    if dispatch is not None:
        overlapping = await find_overlapping_neurons(
            conn, persona_id, candidate.content, candidate.kind, counterparty_id,
        )
        for existing in overlapping:
            if existing["id"] == neuron_id:
                continue
            try:
                result = await check_contradiction(
                    dispatch,
                    persona_id=persona_id,
                    neuron_a_content=existing["content"],
                    neuron_b_content=candidate.content,
                    entity_key=_derive_entity_key(candidate.content, existing["content"]),
                )
                if result.relation == "contradict":
                    await supersede_neuron(conn, existing["id"], neuron_id)
                    # Rule 8: emit supersession event
                    await _emit_neuron_event(
                        conn, persona_id, private_key, public_key_b64,
                        event_type="operator_action",
                        payload={
                            "action": "supersede",
                            "old_neuron_id": existing["id"],
                            "new_neuron_id": neuron_id,
                            "reason": result.reason,
                        },
                    )
            except Exception:
                logger.warning(
                    "Supersession failed for neuron %d", existing["id"],
                    exc_info=True,
                )

    # Rule 8: emit neuron creation event
    await _emit_neuron_event(
        conn, persona_id, private_key, public_key_b64,
        event_type="operator_action",
        payload={
            "action": "neuron_created",
            "neuron_id": neuron_id,
            "kind": candidate.kind,
            "tier": candidate.target_tier,
            "source_event_ids": candidate.source_event_ids,
        },
    )

    return neuron_id


async def _reinforce_existing(
    conn: aiosqlite.Connection,
    persona_id: int,
    new_events: list[Event],
) -> int:
    """Bump distinct_source_count for neurons whose source events overlap with new events.

    Rule 15: only counts distinct source events. Same event cited again
    increments source_count but NOT distinct_source_count.

    Returns count of reinforced neurons.
    """
    if not new_events:
        return 0

    new_event_ids = {e.id for e in new_events}
    reinforced = 0

    # Find active neurons for this persona
    cursor = await conn.execute(
        """
        SELECT id, source_event_ids, source_count, distinct_source_count
        FROM neurons
        WHERE persona_id = ? AND superseded_at IS NULL
        """,
        (persona_id,),
    )
    rows = await cursor.fetchall()

    for row in rows:
        existing_ids = set(json.loads(row["source_event_ids"]))
        new_overlap = new_event_ids & existing_ids

        if not new_overlap:
            continue

        # source_count always increments (repetitions count)
        new_source_count = row["source_count"] + len(new_overlap)
        # distinct_source_count only increments for genuinely new sources
        # (in this case the event was already cited, so distinct stays same)
        new_distinct = row["distinct_source_count"]

        # Check if any new events are truly new to this neuron
        truly_new = new_event_ids - existing_ids
        if truly_new:
            new_distinct += len(truly_new)
            # Update source_event_ids to include new ones
            all_ids = existing_ids | truly_new
            new_source_ids = json.dumps(sorted(all_ids))
            await conn.execute(
                """
                UPDATE neurons
                SET source_count = ?,
                    distinct_source_count = ?,
                    source_event_ids = ?
                WHERE id = ?
                """,
                (new_source_count, new_distinct, new_source_ids, row["id"]),
            )
        else:
            await conn.execute(
                "UPDATE neurons SET source_count = ? WHERE id = ?",
                (new_source_count, row["id"]),
            )

        await conn.commit()
        reinforced += 1

    return reinforced


async def _decay_working_memory(
    conn: aiosqlite.Connection,
    persona_id: int,
    half_life_minutes: int,
) -> int:
    """Apply exponential decay to working memory activation.

    activation = initial * 2^(-elapsed_minutes / half_life)

    Returns count of decayed entries.
    """
    now = datetime.now(tz=UTC)

    cursor = await conn.execute(
        "SELECT id, entered_at, activation FROM working_memory WHERE persona_id = ?",
        (persona_id,),
    )
    rows = await cursor.fetchall()

    decayed = 0
    for row in rows:
        entered = datetime.fromisoformat(row["entered_at"]).replace(tzinfo=UTC)
        elapsed_minutes = (now - entered).total_seconds() / 60.0
        new_activation = row["activation"] * math.pow(2, -elapsed_minutes / half_life_minutes)

        if abs(new_activation - row["activation"]) > 0.001:
            await conn.execute(
                "UPDATE working_memory SET activation = ? WHERE id = ?",
                (new_activation, row["id"]),
            )
            decayed += 1

    if decayed:
        await conn.commit()

    return decayed


async def _prune_working_memory(
    conn: aiosqlite.Connection,
    persona_id: int,
    activation_threshold: float,
    capacity: int,
) -> int:
    """Remove working memory entries below threshold or exceeding capacity.

    Returns count of pruned entries.
    """
    # First, prune by activation threshold
    cursor = await conn.execute(
        "DELETE FROM working_memory WHERE persona_id = ? AND activation < ?",
        (persona_id, activation_threshold),
    )
    pruned = cursor.rowcount

    # Then enforce capacity limit (keep highest activation)
    cursor = await conn.execute(
        "SELECT count(*) as c FROM working_memory WHERE persona_id = ?",
        (persona_id,),
    )
    count_row = await cursor.fetchone()
    assert count_row is not None
    total = count_row["c"]

    if total > capacity:
        excess = total - capacity
        cursor = await conn.execute(
            """
            DELETE FROM working_memory
            WHERE id IN (
                SELECT id FROM working_memory
                WHERE persona_id = ?
                ORDER BY activation ASC
                LIMIT ?
            )
            """,
            (persona_id, excess),
        )
        pruned += cursor.rowcount

    if pruned:
        await conn.commit()

    return pruned


async def _emit_neuron_event(
    conn: aiosqlite.Connection,
    persona_id: int,
    private_key: bytes,
    public_key_b64: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Emit an event for a neuron mutation. Rule 8."""
    content_hash = compute_content_hash(payload)
    message = canonical_signing_message(persona_id, content_hash)
    signature = sign(private_key, message)

    await append_event(
        conn,
        persona_id=persona_id,
        counterparty_id=None,
        event_type=event_type,
        scope="private",
        payload=payload,
        signature=signature,
        public_key_b64=public_key_b64,
    )


def _hash_content(content: str) -> str:
    """SHA-256 hash of neuron content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _infer_counterparty(events: list[Event]) -> int | None:
    """Infer counterparty_id from a batch of events. Returns first non-None."""
    for event in events:
        if event.counterparty_id is not None:
            return event.counterparty_id
    return None


def _derive_entity_key(content_a: str, content_b: str) -> str:
    """Derive an entity key from two neuron contents.

    Simple heuristic: longest common noun phrase. For Phase 2, just use
    first 3 significant words from each to form the key.
    """
    words_a = [w.lower() for w in content_a.split()[:5] if len(w) > 3]
    words_b = [w.lower() for w in content_b.split()[:5] if len(w) > 3]
    common = set(words_a) & set(words_b)
    if common:
        return ":".join(sorted(common)[:3])
    return f"{words_a[0]}:{words_b[0]}" if words_a and words_b else "unknown"
