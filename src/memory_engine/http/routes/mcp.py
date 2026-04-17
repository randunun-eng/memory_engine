"""POST /v1/mcp/register — register an MCP signing key for a persona.

Phase 6.5 HTTP surface. Thin wrapper over adapters.whatsapp.mcp.register_mcp.
Bootstrap flow discards the returned bearer token; we return only {id}.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memory_engine.adapters.whatsapp.mcp import register_mcp
from memory_engine.db.connection import connect

router = APIRouter()


class RegisterMCPRequest(BaseModel):
    persona_slug: str
    kind: str
    name: str
    public_key: str


class RegisterMCPResponse(BaseModel):
    id: int


@router.post("/mcp/register", response_model=RegisterMCPResponse, status_code=201)
async def register_mcp_endpoint(req: RegisterMCPRequest) -> RegisterMCPResponse:
    conn = await connect()
    try:
        # Resolve slug → persona_id
        cursor = await conn.execute(
            "SELECT id FROM personas WHERE slug = ?", (req.persona_slug,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Persona {req.persona_slug!r} not found"
            )
        persona_id = int(row["id"])

        mcp, _bearer_token = await register_mcp(
            conn,
            persona_id=persona_id,
            kind=req.kind,
            name=req.name,
            public_key_b64=req.public_key,
        )
        return RegisterMCPResponse(id=mcp.id)
    finally:
        await conn.close()
