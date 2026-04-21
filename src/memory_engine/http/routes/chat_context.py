"""POST /v1/chat_context — per-chat memory bundle.

One endpoint. Returns everything a draft-producing consumer needs about
a specific (persona, counterparty) pair:

  - recent_messages   : last N events for this contact, oldest-first
  - voice_samples     : last N operator outbound texts (for style anchor)
  - top_neurons       : RRF-fused recall results (if query supplied) or
                        top distinct_source_count neurons for this lens
  - episodes          : active (unclosed) episode summaries for this contact
  - synapses          : edges between top_neurons (if any exist)

Architectural intent: consumers (twin-agent, a future UI, the eval
harness, an operator CLI) all call this instead of re-reading bridge
or computing from raw events. Counterparty isolation (rule 12) is
enforced at SQL level — no code path can surface messages from a
different chat.

See DRIFT `chat-context-first-class-primitive`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from memory_engine.db.connection import connect
from memory_engine.retrieval import recall as recall_fn

router = APIRouter()


class ChatContextRequest(BaseModel):
    persona_slug: str
    counterparty_external_ref: str
    query: str | None = None              # optional — drives recall ranking
    recent_limit: int = Field(default=20, ge=0, le=100)
    voice_limit: int = Field(default=15, ge=0, le=100)
    top_k: int = Field(default=10, ge=0, le=50)


class RecentMessage(BaseModel):
    role: str                              # "them" or "me"
    text: str
    ts: str
    event_type: str
    media_type: str | None = None


class EpisodeSummary(BaseModel):
    id: int
    summary: str
    start_event: int
    end_event: int
    created_at: str


class NeuronResult(BaseModel):
    neuron_id: int
    content: str
    kind: str
    tier: str
    scores: dict[str, Any]


class ChatContextResponse(BaseModel):
    persona_id: int
    counterparty_id: int | None
    counterparty_external_ref: str
    recent_messages: list[RecentMessage]
    voice_samples: list[str]
    episodes: list[EpisodeSummary]
    top_neurons: list[NeuronResult]


def _extract_text(payload_raw: str) -> str:
    """Best-effort text from an event payload JSON. Handles media_ocr
    (which stores text under payload.text) and standard text events."""
    try:
        payload = json.loads(payload_raw)
    except Exception:
        return payload_raw or ""
    if isinstance(payload, dict):
        return str(payload.get("text") or payload.get("body") or payload.get("content") or "")
    return str(payload)


@router.post("/chat_context", response_model=ChatContextResponse)
async def chat_context_endpoint(
    req: ChatContextRequest, request: Request
) -> ChatContextResponse:
    """Return the complete per-chat memory bundle.

    Rule 12 enforcement: every read is filtered by `counterparty_id = ?`
    at SQL, so results cannot bleed across chats.
    """
    conn = await connect()
    try:
        # Resolve persona slug → id
        cursor = await conn.execute(
            "SELECT id FROM personas WHERE slug = ?", (req.persona_slug,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Persona {req.persona_slug!r} not found"
            )
        persona_id = int(row["id"])

        # Resolve counterparty external_ref → id (may be None if unknown)
        cursor = await conn.execute(
            "SELECT id FROM counterparties WHERE persona_id = ? AND external_ref = ?",
            (persona_id, req.counterparty_external_ref),
        )
        cp_row = await cursor.fetchone()
        counterparty_id: int | None = int(cp_row["id"]) if cp_row else None

        # --- recent_messages ---
        recent_messages: list[RecentMessage] = []
        if req.recent_limit > 0 and counterparty_id is not None:
            cursor = await conn.execute(
                """
                SELECT type, payload, recorded_at
                FROM events
                WHERE persona_id = ? AND counterparty_id = ?
                  AND type IN ('message_in', 'message_out')
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (persona_id, counterparty_id, req.recent_limit),
            )
            rows = await cursor.fetchall()
            # reverse to chronological (oldest-first) for prompt use
            for row in reversed(list(rows)):
                text = _extract_text(row["payload"])
                if not text:
                    continue
                try:
                    payload = json.loads(row["payload"])
                    media_type = payload.get("media_type") if isinstance(payload, dict) else None
                except Exception:
                    media_type = None
                recent_messages.append(
                    RecentMessage(
                        role="me" if row["type"] == "message_out" else "them",
                        text=text,
                        ts=str(row["recorded_at"]),
                        event_type=row["type"],
                        media_type=media_type,
                    )
                )

        # --- voice_samples (operator's outbound only) ---
        voice_samples: list[str] = []
        if req.voice_limit > 0 and counterparty_id is not None:
            cursor = await conn.execute(
                """
                SELECT payload
                FROM events
                WHERE persona_id = ? AND counterparty_id = ?
                  AND type = 'message_out'
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (persona_id, counterparty_id, req.voice_limit),
            )
            for row in reversed(list(await cursor.fetchall())):
                text = _extract_text(row["payload"])
                if text:
                    voice_samples.append(text)

        # --- episodes (active, unclosed) ---
        episodes: list[EpisodeSummary] = []
        if counterparty_id is not None:
            try:
                cursor = await conn.execute(
                    """
                    SELECT id, summary, start_event, end_event, created_at
                    FROM episodes
                    WHERE persona_id = ? AND counterparty_id = ?
                      AND summary IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    (persona_id, counterparty_id),
                )
                for row in await cursor.fetchall():
                    episodes.append(
                        EpisodeSummary(
                            id=int(row["id"]),
                            summary=str(row["summary"]),
                            start_event=int(row["start_event"]),
                            end_event=int(row["end_event"]),
                            created_at=str(row["created_at"]),
                        )
                    )
            except Exception:
                # episodes.counterparty_id may not exist on older DBs —
                # migration 009 is additive but we stay graceful.
                pass

        # --- top_neurons (via recall if query given, else distinct-source-rank) ---
        top_neurons: list[NeuronResult] = []
        if req.top_k > 0:
            if req.query:
                # Use the full recall fusion pipeline
                embed_fn = getattr(request.app.state, "embed_fn", None)
                query_embedding: list[float] | None = None
                embedder_rev: str | None = None
                if embed_fn is not None:
                    try:
                        query_embedding = list(embed_fn(req.query))
                        embedder_rev = getattr(
                            request.app.state, "embedder_rev",
                            "paraphrase-multilingual-minilm-l12-v2-1",
                        )
                    except Exception:
                        query_embedding = None

                lens = f"counterparty:{req.counterparty_external_ref}"
                results = await recall_fn(
                    conn,
                    persona_id=persona_id,
                    query=req.query,
                    lens=lens,
                    top_k=req.top_k,
                    query_embedding=query_embedding,
                    embedder_rev=embedder_rev,
                )
                for r in results:
                    top_neurons.append(
                        NeuronResult(
                            neuron_id=r.neuron.id,
                            content=r.neuron.content,
                            kind=r.neuron.kind,
                            tier=r.neuron.tier,
                            scores={
                                "bm25": r.scores.bm25,
                                "vector": r.scores.vector,
                                "graph": r.scores.graph,
                                "fused": r.scores.fused,
                                "rank_sources": list(r.scores.rank_sources),
                            },
                        )
                    )
            elif counterparty_id is not None:
                # No query — return top distinct-source-count neurons
                # scoped to this counterparty. Rule 15: rank by
                # distinct_source_count, not source_count.
                cursor = await conn.execute(
                    """
                    SELECT id, content, kind, tier, distinct_source_count, source_count
                    FROM neurons
                    WHERE persona_id = ?
                      AND (counterparty_id = ? OR kind = 'domain_fact')
                      AND superseded_at IS NULL
                    ORDER BY distinct_source_count DESC, id DESC
                    LIMIT ?
                    """,
                    (persona_id, counterparty_id, req.top_k),
                )
                for row in await cursor.fetchall():
                    top_neurons.append(
                        NeuronResult(
                            neuron_id=int(row["id"]),
                            content=str(row["content"]),
                            kind=str(row["kind"]),
                            tier=str(row["tier"]),
                            scores={
                                "distinct_source_count": int(row["distinct_source_count"]),
                                "source_count": int(row["source_count"]),
                            },
                        )
                    )

        return ChatContextResponse(
            persona_id=persona_id,
            counterparty_id=counterparty_id,
            counterparty_external_ref=req.counterparty_external_ref,
            recent_messages=recent_messages,
            voice_samples=voice_samples,
            episodes=episodes,
            top_neurons=top_neurons,
        )
    finally:
        await conn.close()
