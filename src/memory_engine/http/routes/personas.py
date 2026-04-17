"""POST /v1/personas — create a persona row.

Phase 6.5 HTTP surface. Thin wrapper; see docs/blueprint/DRIFT.md for
the shortcuts taken (owner_public_key not persisted — identity signature
verification deferred).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from memory_engine.db.connection import connect

router = APIRouter()


class CreatePersonaRequest(BaseModel):
    slug: str
    owner_public_key: str | None = None  # accepted but not persisted — see DRIFT


class CreatePersonaResponse(BaseModel):
    id: int
    slug: str


@router.post("/personas", response_model=CreatePersonaResponse, status_code=201)
async def create_persona(req: CreatePersonaRequest) -> CreatePersonaResponse:
    conn = await connect()
    try:
        cursor = await conn.execute(
            "INSERT INTO personas (slug) VALUES (?)",
            (req.slug,),
        )
        await conn.commit()
        pid = cursor.lastrowid
        assert pid is not None
        return CreatePersonaResponse(id=int(pid), slug=req.slug)
    finally:
        await conn.close()
