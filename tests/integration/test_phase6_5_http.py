"""Phase 6.5 HTTP surface — smoke tests.

One smoke test per endpoint. Asserts:
  - 2xx status on valid JSON
  - Side effect visible in DB (or response reflects correct row)

Not a full test suite. Edge cases and error paths deferred. See
docs/blueprint/DRIFT.md entries:
  - create_persona-inline-SQL
  - ingest-3-lookups-inline-SQL
  - identity-load-signature-not-verified
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    import aiosqlite


@pytest.fixture
def http_client(db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a TestClient that talks to the same DB as the `db` fixture.

    The `db` fixture (in conftest.py) creates a migrated SQLite file at
    tmp_path/test.db. We steer the FastAPI app's `connect()` calls to the
    same file by patching the settings singleton directly — env var
    overrides don't work because Settings.load() passes TOML data as
    kwargs which outrank env-var reads.
    """
    db_path = str(tmp_path / "test.db")

    from memory_engine.config import DBSettings, Settings

    test_settings = Settings(db=DBSettings(path=db_path))

    import memory_engine.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_settings", test_settings)

    from memory_engine.http.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


# ---- Health (sanity) ----


def test_health_returns_ok(http_client: TestClient) -> None:
    r = http_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---- POST /v1/personas ----


async def test_create_persona_smoke(db: aiosqlite.Connection, http_client: TestClient) -> None:
    r = http_client.post(
        "/v1/personas",
        json={"slug": "smoke_persona", "owner_public_key": "ignored_for_alpha"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "smoke_persona"
    assert isinstance(body["id"], int)

    # Side effect: row exists
    cursor = await db.execute("SELECT slug FROM personas WHERE id = ?", (body["id"],))
    row = await cursor.fetchone()
    assert row is not None
    assert row["slug"] == "smoke_persona"


# ---- POST /v1/mcp/register ----


async def test_register_mcp_smoke(db: aiosqlite.Connection, http_client: TestClient) -> None:
    # Create persona via the HTTP endpoint (dogfood)
    r = http_client.post("/v1/personas", json={"slug": "mcp_smoke", "owner_public_key": "x"})
    assert r.status_code == 201, r.text

    # Generate a real Ed25519 public key
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    pub_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")

    r = http_client.post(
        "/v1/mcp/register",
        json={
            "persona_slug": "mcp_smoke",
            "kind": "whatsapp",
            "name": "primary",
            "public_key": pub_b64,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["id"], int)

    # Side effect: row exists + public_key round-trips
    cursor = await db.execute(
        "SELECT kind, name, public_key_ed25519 FROM mcp_sources WHERE id = ?",
        (body["id"],),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["kind"] == "whatsapp"
    assert row["name"] == "primary"
    assert row["public_key_ed25519"] == pub_b64


# ---- POST /v1/identity/load ----


async def test_load_identity_smoke(db: aiosqlite.Connection, http_client: TestClient) -> None:
    # Create persona
    r = http_client.post("/v1/personas", json={"slug": "identity_smoke"})
    assert r.status_code == 201

    # Minimal valid identity YAML per parse_identity_yaml's required fields
    yaml_text = """\
persona: identity_smoke
version: 1
signed_by: smoke@example.org
signed_at: 2026-04-17T10:00:00Z

self_facts:
  - text: "Smoke test persona."
    confidence: 1.0

non_negotiables:
  - "I never disclose test secrets."

forbidden_topics:
  - politics

deletion_policy:
  inbound: ignore
  outbound: honor
"""

    r = http_client.post(
        "/v1/identity/load",
        content=yaml_text.encode("utf-8"),
        headers={"Content-Type": "application/x-yaml; charset=utf-8"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["persona_id"], int)

    # Side effect: identity_doc is now set on the persona
    cursor = await db.execute(
        "SELECT identity_doc FROM personas WHERE id = ?", (body["persona_id"],)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["identity_doc"] is not None
    assert "identity_smoke" in row["identity_doc"]


# ---- POST /v1/ingest ----


async def test_ingest_smoke(db: aiosqlite.Connection, http_client: TestClient) -> None:
    # Persona + MCP setup
    r = http_client.post("/v1/personas", json={"slug": "ingest_smoke"})
    assert r.status_code == 201
    persona_id = r.json()["id"]

    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    pub_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")

    r = http_client.post(
        "/v1/mcp/register",
        json={
            "persona_slug": "ingest_smoke",
            "kind": "whatsapp",
            "name": "primary",
            "public_key": pub_b64,
        },
    )
    assert r.status_code == 201

    # Build signed event identical to twin-agent's format
    payload = {
        "text": "Hello from smoke test",
        "wa_message_id": "smoke-1",
        "chat_jid": "94771234567@s.whatsapp.net",
        "timestamp": "2026-04-17T10:00:00Z",
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    content_hash = hashlib.sha256(canonical).hexdigest()
    sig_bytes = sk.sign(f"{persona_id}:{content_hash}".encode()).signature
    signature_b64 = base64.b64encode(sig_bytes).decode("ascii")

    r = http_client.post(
        "/v1/ingest",
        json={
            "persona_slug": "ingest_smoke",
            "counterparty_external_ref": "whatsapp:+94771234567",
            "event_type": "message_in",
            "scope": "private",
            "payload": payload,
            "signature": signature_b64,
            "idempotency_key": "wa:smoke-1",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["event_id"], int)
    assert body["ingested_at"]

    # Side effect: event row exists with expected fields
    cursor = await db.execute(
        "SELECT type, scope, idempotency_key, counterparty_id FROM events WHERE id = ?",
        (body["event_id"],),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["type"] == "message_in"
    assert row["scope"] == "private"
    assert row["idempotency_key"] == "wa:smoke-1"
    assert row["counterparty_id"] is not None
