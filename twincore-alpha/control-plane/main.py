"""
TwinCore Alpha — Control Plane.

Responsibilities:
1. Accept draft submissions from twin-agent
2. Expose approval/rejection endpoints
3. Global halt via file-based kill switch
4. Send approved drafts via whatsapp-bridge HTTP
5. Block specific contacts via config
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("control-plane")

# ---------- Config ----------

DB_PATH = os.environ.get("CONTROL_PLANE_DB_PATH", "/app/data/control.db")
WHATSAPP_BRIDGE_URL = os.environ["WHATSAPP_BRIDGE_URL"]
# Operator's own WhatsApp JID (α.1). When a draft is queued, a notification
# is sent to this chat so the operator can /approve, /reject, or /edit from
# WhatsApp directly. Empty string disables the notification.
# OWN_JID may be a comma-separated list (phone JID, LID, etc.); we SEND to
# the first entry only. Notification delivery uses phone-format typically.
_own_jid_raw = os.environ.get("OWN_JID", "")
_own_jids = [j.strip() for j in _own_jid_raw.split(",") if j.strip()]
OWN_JID_SEND: str = _own_jids[0] if _own_jids else ""
PAUSE_FILE = Path("/var/twincore/PAUSE")
BLOCKED_FILE = Path("/var/twincore/blocked_contacts.json")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("/var/twincore").mkdir(parents=True, exist_ok=True)

# ---------- Database ----------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA encoding = 'UTF-8'")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              persona_slug TEXT NOT NULL,
              counterparty TEXT NOT NULL,
              chat_jid TEXT NOT NULL,
              -- TEXT, not INTEGER: WhatsApp/whatsmeow IDs are strings
              -- (e.g. "3EB0A4C2B8F1234567"). See twincore-alpha/DRIFT.md
              -- `twin-agent-assumed-integer-message-id` (propagated here).
              incoming_msg_id TEXT NOT NULL,
              incoming_text TEXT NOT NULL,
              draft_text TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              decided_at TEXT,
              sent_at TEXT,
              error TEXT,
              UNIQUE (incoming_msg_id, persona_slug)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_drafts_status ON drafts(status)")

        # α.2 Contact profiles — per-(persona, counterparty) relationship +
        # display name used to enrich the persona system prompt.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_profiles (
              id             INTEGER PRIMARY KEY AUTOINCREMENT,
              persona_slug   TEXT NOT NULL,
              external_ref   TEXT NOT NULL,
              relationship   TEXT NOT NULL,
              display_name   TEXT,
              added_at       TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
              UNIQUE (persona_slug, external_ref)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_contact_profiles_ref "
            "ON contact_profiles(persona_slug, external_ref)"
        )
        conn.commit()


init_db()

# ---------- Blocked contacts helper ----------

def is_blocked(counterparty: str) -> bool:
    if not BLOCKED_FILE.exists():
        return False
    try:
        data = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
        blocked = set(data.get("blocked_contacts", []))
        return counterparty in blocked
    except Exception:
        return False


def update_blocked(counterparty: str, block: bool) -> dict:
    if BLOCKED_FILE.exists():
        data = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
    else:
        data = {"blocked_contacts": []}
    s = set(data.get("blocked_contacts", []))
    if block:
        s.add(counterparty)
    else:
        s.discard(counterparty)
    data["blocked_contacts"] = sorted(s)
    BLOCKED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# ---------- App ----------

app = FastAPI(title="TwinCore Control Plane")


# ---------- Models ----------

class DraftIn(BaseModel):
    persona_slug: str
    counterparty: str
    chat_jid: str
    # str, not int: whatsmeow IDs are TEXT. See DRIFT.md
    # `twin-agent-assumed-integer-message-id`.
    incoming_msg_id: str
    incoming_text: str
    draft_text: str


class BlockRequest(BaseModel):
    counterparty: str


class EditDraftRequest(BaseModel):
    new_text: str


class ContactProfileIn(BaseModel):
    persona_slug: str
    external_ref: str
    relationship: str
    display_name: str | None = None


# ---------- Self-chat notification (α.1) ----------

async def notify_self_chat_new_draft(
    draft_id: int,
    counterparty: str,
    incoming_text: str,
    draft_text: str,
) -> None:
    """Send a formatted draft-notification to the operator's own WhatsApp chat.
    Silent no-op if OWN_JID is not configured or bridge is unreachable.
    """
    if not OWN_JID_SEND:
        return

    # Format for easy thumb-scanning. Truncate long content so the
    # notification stays readable in the WhatsApp chat.
    def _truncate(s: str, n: int) -> str:
        s = s.replace("\n", " ")
        return s if len(s) <= n else s[: n - 1] + "\u2026"

    body = (
        f"[#{draft_id}] {counterparty}\n"
        f"They: \"{_truncate(incoming_text, 200)}\"\n"
        f"Draft: \"{_truncate(draft_text, 400)}\"\n\n"
        f"/approve {draft_id}  /reject {draft_id}  /edit {draft_id} <text>"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{WHATSAPP_BRIDGE_URL}/api/send",
                json={"recipient": OWN_JID_SEND, "message": body},
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            if r.status_code >= 400:
                log.warning(
                    "Self-chat notification failed: %d %s",
                    r.status_code, r.text[:200],
                )
    except Exception as e:
        # Don't let notification failure break draft creation
        log.warning("Self-chat notification error: %s", e)


# ---------- Internal (from twin-agent) ----------

@app.post("/internal/drafts")
async def submit_draft(draft: DraftIn) -> dict:
    """Called by twin-agent to queue a draft."""
    try:
        with db() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO drafts
                    (persona_slug, counterparty, chat_jid, incoming_msg_id,
                     incoming_text, draft_text, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    draft.persona_slug,
                    draft.counterparty,
                    draft.chat_jid,
                    draft.incoming_msg_id,
                    draft.incoming_text,
                    draft.draft_text,
                ),
            )
            conn.commit()
            if cur.rowcount == 0:
                log.info("Duplicate draft for msg_id=%s (already queued)",
                         draft.incoming_msg_id)
                return {"status": "duplicate"}

            draft_id = cur.lastrowid
            log.info("Queued draft id=%d for %s", draft_id, draft.counterparty)

        # Notify self-chat (outside the DB transaction)
        await notify_self_chat_new_draft(
            draft_id=draft_id,
            counterparty=draft.counterparty,
            incoming_text=draft.incoming_text,
            draft_text=draft.draft_text,
        )

        return {"status": "queued", "draft_id": draft_id}
    except Exception as e:
        log.exception("submit_draft failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Contact profiles (α.2) ----------

@app.post("/internal/contact_profiles")
async def upsert_contact_profile(p: ContactProfileIn) -> dict:
    """Create or update a contact profile. Used by twin-agent's /contact
    command to classify counterparties."""
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO contact_profiles
                    (persona_slug, external_ref, relationship, display_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(persona_slug, external_ref) DO UPDATE SET
                    relationship = excluded.relationship,
                    display_name = excluded.display_name,
                    updated_at   = datetime('now')
                """,
                (p.persona_slug, p.external_ref, p.relationship, p.display_name),
            )
            conn.commit()
        log.info(
            "contact_profile upserted: %s %s=%s (%s)",
            p.persona_slug, p.external_ref, p.relationship, p.display_name,
        )
        return {
            "status": "ok",
            "external_ref": p.external_ref,
            "relationship": p.relationship,
            "display_name": p.display_name,
        }
    except Exception as e:
        log.exception("upsert_contact_profile failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/internal/contact_profiles/{external_ref:path}")
async def get_contact_profile(external_ref: str) -> dict:
    """Look up a contact profile by external_ref. URL-decoded, path-style
    so that colons and @ in the ref survive transit."""
    with db() as conn:
        row = conn.execute(
            """
            SELECT external_ref, relationship, display_name,
                   added_at, updated_at
              FROM contact_profiles
             WHERE external_ref = ?
             LIMIT 1
            """,
            (external_ref,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no profile")
    return dict(row)


# ---------- Operator-facing ----------

@app.get("/drafts")
async def list_drafts(status: str = "pending", limit: int = 50) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, persona_slug, counterparty, incoming_text, draft_text,
                   status, created_at, decided_at, sent_at, error
              FROM drafts
             WHERE status = ?
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int) -> dict:
    if PAUSE_FILE.exists():
        raise HTTPException(status_code=423, detail="System is paused (kill switch)")

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Draft not found")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Draft is {row['status']}, not pending",
            )

        counterparty = row["counterparty"]
        if is_blocked(counterparty):
            raise HTTPException(
                status_code=403,
                detail=f"Contact {counterparty} is blocked",
            )

        chat_jid = row["chat_jid"]
        draft_text = row["draft_text"]

    # Send via whatsapp-bridge
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{WHATSAPP_BRIDGE_URL}/api/send",
                json={
                    "recipient": chat_jid,
                    "message": draft_text,
                },
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            if r.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Bridge error: {r.status_code} {r.text[:200]}",
                )
    except httpx.HTTPError as e:
        error = str(e)
        with db() as conn:
            conn.execute(
                """
                UPDATE drafts SET status='failed', decided_at=datetime('now'),
                                  error=?
                 WHERE id=?
                """,
                (error, draft_id),
            )
            conn.commit()
        raise HTTPException(status_code=502, detail=error)

    # Mark sent
    with db() as conn:
        conn.execute(
            """
            UPDATE drafts SET status='sent',
                              decided_at=datetime('now'),
                              sent_at=datetime('now')
             WHERE id=?
            """,
            (draft_id,),
        )
        conn.commit()

    log.info("Sent draft id=%d to %s", draft_id, counterparty)
    return {"status": "sent", "draft_id": draft_id}


@app.post("/drafts/{draft_id}/reject")
async def reject_draft(draft_id: int) -> dict:
    with db() as conn:
        cur = conn.execute(
            """
            UPDATE drafts SET status='rejected', decided_at=datetime('now')
             WHERE id=? AND status='pending'
            """,
            (draft_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Draft not found or not pending")
    log.info("Rejected draft id=%d", draft_id)
    return {"status": "rejected", "draft_id": draft_id}


@app.post("/drafts/{draft_id}/edit")
async def edit_and_send_draft(draft_id: int, req: EditDraftRequest) -> dict:
    """Update a pending draft's text, then send it. Used by the /edit
    self-chat command to correct tone/content inline without a two-step
    approve after modify."""
    if PAUSE_FILE.exists():
        raise HTTPException(status_code=423, detail="System is paused (kill switch)")

    new_text = req.new_text.strip()
    if not new_text:
        raise HTTPException(status_code=400, detail="new_text is empty")

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Draft not found")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Draft is {row['status']}, not pending",
            )

        counterparty = row["counterparty"]
        chat_jid = row["chat_jid"]
        if is_blocked(counterparty):
            raise HTTPException(
                status_code=403,
                detail=f"Contact {counterparty} is blocked",
            )

        # Update text in place so the audit trail reflects what was sent
        conn.execute(
            "UPDATE drafts SET draft_text = ? WHERE id = ?",
            (new_text, draft_id),
        )
        conn.commit()

    # Send via whatsapp-bridge
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{WHATSAPP_BRIDGE_URL}/api/send",
                json={"recipient": chat_jid, "message": new_text},
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            if r.status_code >= 400:
                with db() as conn:
                    conn.execute(
                        """UPDATE drafts SET status='failed',
                                             decided_at=datetime('now'),
                                             error=? WHERE id=?""",
                        (f"bridge {r.status_code}: {r.text[:200]}", draft_id),
                    )
                    conn.commit()
                raise HTTPException(
                    status_code=502,
                    detail=f"Bridge error: {r.status_code} {r.text[:200]}",
                )
    except httpx.HTTPError as e:
        with db() as conn:
            conn.execute(
                """UPDATE drafts SET status='failed',
                                     decided_at=datetime('now'),
                                     error=? WHERE id=?""",
                (str(e), draft_id),
            )
            conn.commit()
        raise HTTPException(status_code=502, detail=str(e))

    with db() as conn:
        conn.execute(
            """UPDATE drafts SET status='sent',
                                 decided_at=datetime('now'),
                                 sent_at=datetime('now')
                WHERE id=?""",
            (draft_id,),
        )
        conn.commit()

    log.info("Edited+sent draft id=%d to %s", draft_id, counterparty)
    return {"status": "sent", "draft_id": draft_id, "sent_text": new_text}


@app.post("/halt")
async def halt() -> dict:
    PAUSE_FILE.touch()
    log.warning("HALT activated — no sends until /resume")
    return {"status": "halted"}


@app.post("/resume")
async def resume() -> dict:
    try:
        PAUSE_FILE.unlink()
    except FileNotFoundError:
        pass
    log.info("Resumed")
    return {"status": "resumed"}


@app.get("/status")
async def status() -> dict:
    with db() as conn:
        counts = {}
        for s in ("pending", "sent", "rejected", "failed"):
            counts[s] = conn.execute(
                "SELECT count(*) FROM drafts WHERE status=?", (s,)
            ).fetchone()[0]
    blocked = []
    if BLOCKED_FILE.exists():
        try:
            blocked = json.loads(BLOCKED_FILE.read_text(encoding="utf-8")).get(
                "blocked_contacts", []
            )
        except Exception:
            pass
    return {
        "paused": PAUSE_FILE.exists(),
        "counts": counts,
        "blocked_contacts": blocked,
    }


@app.post("/block")
async def block_contact(req: BlockRequest) -> dict:
    update_blocked(req.counterparty, block=True)
    log.warning("Blocked contact: %s", req.counterparty)
    return {"status": "blocked", "counterparty": req.counterparty}


@app.post("/unblock")
async def unblock_contact(req: BlockRequest) -> dict:
    update_blocked(req.counterparty, block=False)
    log.info("Unblocked contact: %s", req.counterparty)
    return {"status": "unblocked", "counterparty": req.counterparty}


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    with db() as conn:
        drafts = conn.execute(
            """
            SELECT id, counterparty, incoming_text, draft_text, created_at
              FROM drafts WHERE status='pending' ORDER BY id DESC LIMIT 20
            """
        ).fetchall()
    paused = PAUSE_FILE.exists()
    rows_html = ""
    for r in drafts:
        rows_html += f"""
        <div style='border:1px solid #ccc; padding:10px; margin:10px 0; border-radius:6px;'>
          <div><b>#{r['id']}</b> from <code>{r['counterparty']}</code> at {r['created_at']}</div>
          <div style='color:#666; margin-top:6px;'>They said: {r['incoming_text']}</div>
          <div style='margin-top:6px;'>Draft: <b>{r['draft_text']}</b></div>
          <div style='margin-top:10px;'>
            <button onclick="fetch('/drafts/{r['id']}/approve',{{method:'POST'}}).then(()=>location.reload())">Approve &amp; send</button>
            <button onclick="fetch('/drafts/{r['id']}/reject',{{method:'POST'}}).then(()=>location.reload())">Reject</button>
          </div>
        </div>
        """
    if paused:
        pause_banner = "<div style='background:#fee; padding:10px; color:#c00; border-radius:6px;'><b>HALTED</b> — <button onclick=\"fetch('/resume',{method:'POST'}).then(()=>location.reload())\">Resume</button></div>"
    else:
        pause_banner = "<div><button onclick=\"fetch('/halt',{method:'POST'}).then(()=>location.reload())\">Halt everything</button></div>"
    empty = "<p>No pending drafts.</p>" if not drafts else ""
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>TwinCore Control Plane</title>
<style>body{{font-family:system-ui;max-width:800px;margin:20px auto;padding:0 20px;}}button{{padding:6px 12px;margin-right:6px;cursor:pointer;}}</style>
</head><body>
<h1>TwinCore Control Plane</h1>
{pause_banner}
<h2>Pending drafts ({len(drafts)})</h2>
{rows_html}{empty}
</body></html>"""
