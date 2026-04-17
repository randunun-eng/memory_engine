"""POST /v1/ingest — signed event ingest from a registered MCP.

Phase 6.5 HTTP surface. Thin wrapper over core.events.append_event.

Three DB lookups inline before calling append_event:
  1. slug → persona_id
  2. (persona_id, external_ref) → counterparty_id (create if missing)
  3. persona_id → active mcp_source (for public_key + mcp_source_id)

See DRIFT entry `ingest-3-lookups-inline-SQL`. If a third call site ever
needs the same resolution, extract into a shared _resolve_ingest_context().
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from memory_engine.core.events import append_event
from memory_engine.db.connection import connect

router = APIRouter()


class IngestRequest(BaseModel):
    persona_slug: str
    counterparty_external_ref: str | None = None
    event_type: str
    scope: str = Field(pattern="^(private|shared|public)$")
    payload: dict[str, Any]
    signature: str
    idempotency_key: str | None = None
    sender_hint: str | None = None


class IngestResponse(BaseModel):
    event_id: int
    ingested_at: str


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest_endpoint(req: IngestRequest) -> IngestResponse:
    conn = await connect()
    try:
        # 1. slug → persona_id
        cursor = await conn.execute(
            "SELECT id FROM personas WHERE slug = ?", (req.persona_slug,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Persona {req.persona_slug!r} not found"
            )
        persona_id = int(row["id"])

        # 2. external_ref → counterparty_id (lookup or create)
        counterparty_id: int | None = None
        if req.counterparty_external_ref:
            cursor = await conn.execute(
                "SELECT id FROM counterparties WHERE persona_id = ? AND external_ref = ?",
                (persona_id, req.counterparty_external_ref),
            )
            cp_row = await cursor.fetchone()
            if cp_row is None:
                cursor = await conn.execute(
                    "INSERT INTO counterparties (persona_id, external_ref) VALUES (?, ?)",
                    (persona_id, req.counterparty_external_ref),
                )
                await conn.commit()
                assert cursor.lastrowid is not None
                counterparty_id = int(cursor.lastrowid)
            else:
                counterparty_id = int(cp_row["id"])

        # 3. persona_id → active mcp_source (picks one; alpha supports one MCP per persona)
        cursor = await conn.execute(
            """
            SELECT id, public_key_ed25519 FROM mcp_sources
            WHERE persona_id = ? AND revoked_at IS NULL
            ORDER BY registered_at DESC LIMIT 1
            """,
            (persona_id,),
        )
        mcp_row = await cursor.fetchone()
        if mcp_row is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No active MCP registered for persona {req.persona_slug!r}; "
                    "call POST /v1/mcp/register first"
                ),
            )
        mcp_source_id = int(mcp_row["id"])
        public_key_b64 = mcp_row["public_key_ed25519"]

        # 4. append_event verifies signature and persists.
        event = await append_event(
            conn,
            persona_id=persona_id,
            counterparty_id=counterparty_id,
            event_type=req.event_type,
            scope=req.scope,  # type: ignore[arg-type]
            payload=req.payload,
            signature=req.signature,
            public_key_b64=public_key_b64,
            idempotency_key=req.idempotency_key,
            mcp_source_id=mcp_source_id,
            sender_hint=req.sender_hint,
        )

        return IngestResponse(
            event_id=event.id, ingested_at=event.recorded_at.isoformat()
        )
    finally:
        await conn.close()
