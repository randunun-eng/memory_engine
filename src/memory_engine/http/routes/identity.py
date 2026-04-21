"""POST /v1/identity/load — load a signed identity YAML for a persona.

Phase 6.5 HTTP surface. Thin wrapper over identity.persona.save_identity.
Accepts raw YAML in the request body (not JSON-wrapped). Extracts the
`persona` slug from the YAML itself, resolves to id, then saves.

Does NOT verify the YAML's signature. See DRIFT entry
`identity-load-signature-not-verified`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from memory_engine.db.connection import connect
from memory_engine.identity.persona import parse_identity_yaml, save_identity

router = APIRouter()


class LoadIdentityResponse(BaseModel):
    ok: bool
    persona_id: int
    version: int


@router.post("/identity/load", response_model=LoadIdentityResponse)
async def load_identity_endpoint(request: Request) -> LoadIdentityResponse:
    raw = await request.body()
    yaml_text = raw.decode("utf-8")

    # parse_identity_yaml validates required fields and raises ConfigError
    # if malformed. That propagates to the global handler as 400.
    doc = parse_identity_yaml(yaml_text)

    conn = await connect()
    try:
        cursor = await conn.execute("SELECT id FROM personas WHERE slug = ?", (doc.persona_slug,))
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Persona {doc.persona_slug!r} not found",
            )
        persona_id = int(row["id"])

        saved = await save_identity(conn, persona_id, yaml_text)
        return LoadIdentityResponse(ok=True, persona_id=persona_id, version=saved.version)
    finally:
        await conn.close()
