"""POST /v1/personas — create a persona row.

Phase 6.5 HTTP surface. Migration 008 added personas.owner_public_key;
this endpoint now persists the field when provided. Consolidator
(src/memory_engine/http/lifespan.py::_resolve_persona_public_key) reads
it per-tick and falls back to the shared env key when NULL.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from memory_engine.db.connection import connect

router = APIRouter()


class CreatePersonaRequest(BaseModel):
    slug: str
    owner_public_key: str | None = None


class CreatePersonaResponse(BaseModel):
    id: int
    slug: str
    owner_public_key: str | None


@router.post("/personas", response_model=CreatePersonaResponse, status_code=201)
async def create_persona(req: CreatePersonaRequest) -> CreatePersonaResponse:
    conn = await connect()
    try:
        cursor = await conn.execute(
            "INSERT INTO personas (slug, owner_public_key) VALUES (?, ?)",
            (req.slug, req.owner_public_key),
        )
        await conn.commit()
        pid = cursor.lastrowid
        assert pid is not None
        return CreatePersonaResponse(
            id=int(pid), slug=req.slug, owner_public_key=req.owner_public_key,
        )
    finally:
        await conn.close()
