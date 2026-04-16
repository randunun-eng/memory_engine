"""POST /v1/recall — retrieval endpoint."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from memory_engine.db.connection import connect
from memory_engine.retrieval import recall

router = APIRouter()


class RecallRequest(BaseModel):
    persona_slug: str
    query: str
    lens: str = "auto"
    top_k: int = Field(default=10, ge=1, le=100)
    token_budget: int | None = None
    as_of: datetime | None = None


class RecallResponse(BaseModel):
    results: list[dict[str, Any]]
    latency_ms: int
    lens_applied: str


@router.post("/recall", response_model=RecallResponse)
async def recall_endpoint(req: RecallRequest) -> RecallResponse:
    """Retrieve relevant neurons for a query under a lens."""
    start = time.monotonic()

    conn = await connect()
    try:
        # Resolve persona slug to id
        cursor = await conn.execute(
            "SELECT id FROM personas WHERE slug = ?", (req.persona_slug,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Persona {req.persona_slug!r} not found")
        persona_id: int = row["id"]

        as_of = req.as_of.replace(tzinfo=UTC) if req.as_of and req.as_of.tzinfo is None else req.as_of

        results = await recall(
            conn,
            persona_id=persona_id,
            query=req.query,
            lens=req.lens,
            as_of=as_of,
            top_k=req.top_k,
            token_budget=req.token_budget,
        )
    finally:
        await conn.close()

    elapsed_ms = int((time.monotonic() - start) * 1000)

    return RecallResponse(
        results=[
            {
                "neuron_id": r.neuron.id,
                "content": r.neuron.content,
                "kind": r.neuron.kind,
                "tier": r.neuron.tier,
                "citations": [
                    {"event_id": c.event_id, "recorded_at": c.recorded_at.isoformat()}
                    for c in r.citations
                ],
                "scores": {
                    "bm25": r.scores.bm25,
                    "vector": r.scores.vector,
                    "graph": r.scores.graph,
                    "fused": r.scores.fused,
                },
                "rank_sources": list(r.scores.rank_sources),
            }
            for r in results
        ],
        latency_ms=elapsed_ms,
        lens_applied=req.lens,
    )
