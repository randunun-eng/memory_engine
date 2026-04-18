"""
TwinCore Alpha — Twin Agent.

Responsibilities:
1. Poll lharries' whatsapp-bridge SQLite for new incoming messages
2. Sign and ingest each message to memory_engine
3. Recall relevant context under counterparty lens
4. Call Gemini 2.5 Flash with persona identity + context
5. Submit draft to control-plane for approval
6. Watch for approved drafts, send via whatsapp-bridge HTTP API

Non-goals for alpha:
- Multi-persona routing (single persona hardcoded)
- Group chats (skipped)
- Media (text only)
- Auto-send (all drafts require approval)
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from nacl.signing import SigningKey

# UTF-8 everywhere
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ---------- Config ----------

PERSONA_SLUG = os.environ["PERSONA_SLUG"]
PERSONA_ID = int(os.environ["PERSONA_ID"])
# First-person name used in the system prompt ("You are <NAME>"). Fix for
# identity-leak where the prompt said "You are Randunu's personal assistant"
# and Gemini echoed "This is Randunu's assistant. How can I help you?".
PERSONA_NAME = os.environ.get("PERSONA_NAME", "Randunu")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
)
MEMORY_ENGINE_URL = os.environ["MEMORY_ENGINE_URL"]
MEMORY_ENGINE_MCP_PRIVATE_KEY_B64 = os.environ["MEMORY_ENGINE_MCP_PRIVATE_KEY"]
WHATSAPP_BRIDGE_URL = os.environ["WHATSAPP_BRIDGE_URL"]
WHATSAPP_BRIDGE_DB_PATH = os.environ["WHATSAPP_BRIDGE_DB_PATH"]
CONTROL_PLANE_URL = os.environ["CONTROL_PLANE_URL"]
POLL_INTERVAL = int(os.environ.get("TWIN_POLL_INTERVAL_SEC", "3"))
SYNC_FROM_ISO = os.environ["SYNC_FROM_ISO"]
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "500"))
# Gemini free-tier ceiling for 2.5-flash is 15 RPM. We guard one below so a
# burst never trips 429. Paid tier operators can raise via env.
GEMINI_MAX_RPM = int(os.environ.get("GEMINI_MAX_RPM", "14"))
# When within this many slots of the cap, we log a warning so the operator
# sees creeping saturation before it matters.
GEMINI_WARN_RPM = int(os.environ.get("GEMINI_WARN_RPM", "12"))
PAUSE_FILE = Path("/var/twincore/PAUSE")
# Renamed from last_processed_id — we track timestamp, not ID.
# See twincore-alpha/DRIFT.md `twin-agent-assumed-integer-message-id`.
CHECKPOINT_FILE = Path("/var/twincore/last_processed_ts")
# Separate checkpoint for self-chat commands (α.1).
COMMAND_CHECKPOINT_FILE = Path("/var/twincore/last_command_ts")
IDENTITY_PATH = Path(f"/app/personas/{PERSONA_SLUG}.yaml")

# α.1 Self-chat command interface
# Comma-separated list of operator's own JIDs. whatsmeow stores self-chat
# under the LID format (`<digits>@lid`) on newer WhatsApp versions while
# the phone JID (`<digits>@s.whatsapp.net`) is used for sending. Both must
# be matched when polling commands; the first is used as the send target.
_own_jid_raw = os.environ.get("OWN_JID", "")
OWN_JIDS: list[str] = [j.strip() for j in _own_jid_raw.split(",") if j.strip()]
# For sending, use the first JID in the list (typically the phone-format one).
OWN_JID_SEND: str = OWN_JIDS[0] if OWN_JIDS else ""
OPERATOR_BACKOFF_MINUTES = int(os.environ.get("OPERATOR_BACKOFF_MINUTES", "15"))

MCP_SIGNING_KEY = SigningKey(base64.b64decode(MEMORY_ENGINE_MCP_PRIVATE_KEY_B64))

# ---------- Logging ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("twin-agent")


# ---------- Identity ----------

def load_identity() -> dict:
    """Load signed identity YAML for the persona."""
    with open(IDENTITY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- Checkpoint ----------
# Timestamp-based, not ID-based. See DRIFT.md
# `twin-agent-assumed-integer-message-id`.

def read_checkpoint() -> str:
    """Return last processed message timestamp as ISO string.

    If the checkpoint file is missing or unreadable, defaults to
    SYNC_FROM_ISO — i.e. start from the alpha-activation boundary rather
    than the beginning of time.
    """
    if not CHECKPOINT_FILE.exists():
        return SYNC_FROM_ISO
    try:
        ts = CHECKPOINT_FILE.read_text().strip()
        return ts or SYNC_FROM_ISO
    except OSError:
        return SYNC_FROM_ISO


def write_checkpoint(ts_iso: str) -> None:
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(ts_iso)


# ---------- Self-chat command checkpoint (α.1) ----------

def read_command_checkpoint() -> str:
    """Return last processed self-chat message timestamp (ISO).

    Separate from the conversational checkpoint because self-chat
    messages are processed as commands in a different branch.
    """
    if not COMMAND_CHECKPOINT_FILE.exists():
        return SYNC_FROM_ISO
    try:
        ts = COMMAND_CHECKPOINT_FILE.read_text().strip()
        return ts or SYNC_FROM_ISO
    except OSError:
        return SYNC_FROM_ISO


def write_command_checkpoint(ts_iso: str) -> None:
    COMMAND_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    COMMAND_CHECKPOINT_FILE.write_text(ts_iso)


# ---------- Operator-activity back-off check (α.1) ----------

def operator_replied_recently(chat_jid: str, within_minutes: int | None = None) -> bool:
    """Return True if operator (is_from_me=1) sent a message to this chat
    within the last `within_minutes`. Used to suppress draft generation
    when the operator is actively handling a conversation manually.

    Never matches self-chat (chat_jid in OWN_JIDS) — that's command surface,
    not conversation. Caller should skip self-chat before calling this.
    """
    if not Path(WHATSAPP_BRIDGE_DB_PATH).exists():
        return False

    window = within_minutes if within_minutes is not None else OPERATOR_BACKOFF_MINUTES
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window)
    # Match bridge's timestamp format (space separator — see DRIFT.md
    # `twin-agent-timestamp-separator-mismatch`).
    cutoff_iso = cutoff.strftime("%Y-%m-%d %H:%M:%S+00:00")

    conn = sqlite3.connect(f"file:{WHATSAPP_BRIDGE_DB_PATH}?mode=ro", uri=True)
    try:
        n = conn.execute(
            """
            SELECT COUNT(*) FROM messages
             WHERE chat_jid = ?
               AND is_from_me = 1
               AND timestamp > ?
            """,
            (chat_jid, cutoff_iso),
        ).fetchone()[0]
        return n > 0
    finally:
        conn.close()


# ---------- Self-chat command fetching (α.1) ----------

@dataclass
class SelfMessage:
    id: str
    content: str
    timestamp: datetime


def fetch_new_self_messages(since_ts_iso: str) -> list[SelfMessage]:
    """Fetch self-chat messages (operator writing to their own number) since
    the given checkpoint. Returns ALL such messages — the caller filters
    commands vs. private notes by checking `content.startswith('/')`.

    Matches on any of OWN_JIDS (typically both the phone-format and the
    LID-format JID for the same account — whatsmeow uses them differently).

    Messages NOT starting with `/` are silently ignored by the caller
    (private self-notes — no ingest, no storage in memory_engine).
    """
    if not OWN_JIDS or not Path(WHATSAPP_BRIDGE_DB_PATH).exists():
        return []

    since_ts = datetime.fromisoformat(since_ts_iso.replace("Z", "+00:00"))
    conn = sqlite3.connect(f"file:{WHATSAPP_BRIDGE_DB_PATH}?mode=ro", uri=True)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    conn.row_factory = sqlite3.Row

    placeholders = ",".join("?" for _ in OWN_JIDS)
    query = f"""
        SELECT id, content, timestamp
          FROM messages
         WHERE chat_jid IN ({placeholders})
           AND is_from_me = 1
           AND timestamp > ?
           AND COALESCE(content, '') != ''
         ORDER BY timestamp ASC
         LIMIT 50
    """
    params = (*OWN_JIDS, since_ts.isoformat(sep=" "))

    try:
        cur = conn.execute(query, params)
        results: list[SelfMessage] = []
        for row in cur.fetchall():
            ts_raw = row["timestamp"]
            try:
                if isinstance(ts_raw, str):
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                else:
                    ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            except Exception:
                ts = datetime.now(tz=timezone.utc)
            results.append(SelfMessage(
                id=row["id"],
                content=row["content"] or "",
                timestamp=ts,
            ))
        return results
    finally:
        conn.close()


# ---------- Self-chat reply helper (α.1) ----------

async def send_self_chat(client: httpx.AsyncClient, text: str) -> None:
    """Send a short message back to the operator's own WhatsApp chat.
    Used for command acknowledgments and notifications. Delivers to
    OWN_JID_SEND (the first JID configured — typically phone format).
    """
    if not OWN_JID_SEND:
        log.warning("OWN_JID not configured; cannot send self-chat reply")
        return
    try:
        r = await client.post(
            f"{WHATSAPP_BRIDGE_URL}/api/send",
            json={"recipient": OWN_JID_SEND, "message": text},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10.0,
        )
        if r.status_code >= 400:
            log.error("Self-chat send failed: %d %s", r.status_code, r.text[:200])
    except Exception as e:
        log.exception("Self-chat send error: %s", e)


# ---------- Command parser (α.1) ----------

HELP_TEXT = (
    "/approve N                      — send draft #N\n"
    "/reject N                       — discard draft #N\n"
    "/edit N <text>                  — send draft #N with your text\n"
    "/contact <ref> <relation> <name> — classify a contact\n"
    "   relations: spouse, partner, family, friend, acquaintance, business\n"
    "   ref: phone or LID, e.g. +94771234567 or 9876543210@lid\n"
    "/help                           — this message"
)


def _normalize_contact_ref(raw: str) -> str:
    """Accept flexible contact-ref formats, produce canonical external_ref
    matching what twin-agent stores (whatsapp:+<digits>[@lid]).
    Examples:
      +94777319573           -> whatsapp:+94777319573
      94777319573            -> whatsapp:+94777319573
      +9876543210@lid   -> whatsapp:+9876543210@lid
      9876543210@lid    -> whatsapp:+9876543210@lid
      whatsapp:+X            -> whatsapp:+X (passthrough)
    """
    s = raw.strip()
    if s.startswith("whatsapp:") or s.startswith("whatsapp-group:"):
        return s
    # Strip any leading + so we normalize consistently
    had_plus = s.startswith("+")
    if had_plus:
        s = s[1:]
    # s now should be <digits> or <digits>@lid
    return f"whatsapp:+{s}"


async def handle_command(
    client: httpx.AsyncClient,
    cmd_text: str,
) -> None:
    """Parse and dispatch a command received in self-chat."""
    parts = cmd_text.strip().split(maxsplit=2)
    if not parts:
        return
    verb = parts[0].lower()

    if verb == "/help" or verb == "/?":
        await send_self_chat(client, HELP_TEXT)
        return

    if verb in ("/approve", "/reject"):
        if len(parts) < 2:
            await send_self_chat(client, f"? {verb} needs a draft number. Try /help")
            return
        try:
            draft_id = int(parts[1])
        except ValueError:
            await send_self_chat(client, f"? Not a number: {parts[1]}")
            return

        action = "approve" if verb == "/approve" else "reject"
        try:
            r = await client.post(
                f"{CONTROL_PLANE_URL}/drafts/{draft_id}/{action}",
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=20.0,
            )
            if r.status_code >= 400:
                await send_self_chat(
                    client,
                    f"✗ #{draft_id} {action} failed: {r.status_code} {r.text[:120]}",
                )
            else:
                sym = "✓" if action == "approve" else "✗"
                await send_self_chat(client, f"{sym} #{draft_id} {action}d")
        except Exception as e:
            await send_self_chat(client, f"✗ #{draft_id} error: {str(e)[:120]}")
        return

    if verb == "/edit":
        if len(parts) < 3:
            await send_self_chat(client, "? /edit N <new text>")
            return
        try:
            draft_id = int(parts[1])
        except ValueError:
            await send_self_chat(client, f"? Not a number: {parts[1]}")
            return
        new_text = parts[2]
        try:
            r = await client.post(
                f"{CONTROL_PLANE_URL}/drafts/{draft_id}/edit",
                json={"new_text": new_text},
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=30.0,
            )
            if r.status_code >= 400:
                await send_self_chat(
                    client,
                    f"✗ #{draft_id} edit failed: {r.status_code} {r.text[:120]}",
                )
            else:
                await send_self_chat(client, f"✏ #{draft_id} edited + sent")
        except Exception as e:
            await send_self_chat(client, f"✗ #{draft_id} edit error: {str(e)[:120]}")
        return

    if verb == "/contact":
        # /contact <ref> <relationship> [display_name...]
        # parts[1] = ref, parts[2] = "relationship display_name..."
        if len(parts) < 3:
            await send_self_chat(
                client,
                "? /contact <ref> <relationship> [name]\n"
                "   relations: spouse, partner, family, friend, acquaintance, business",
            )
            return
        ref = _normalize_contact_ref(parts[1])
        rel_and_name = parts[2].split(maxsplit=1)
        relationship = rel_and_name[0].lower()
        display_name = rel_and_name[1].strip() if len(rel_and_name) > 1 else None

        if relationship not in RELATIONSHIP_TONES:
            await send_self_chat(
                client,
                f"? Unknown relationship '{relationship}'. "
                f"Use one of: {', '.join(sorted(RELATIONSHIP_TONES.keys()))}",
            )
            return

        try:
            r = await client.post(
                f"{CONTROL_PLANE_URL}/internal/contact_profiles",
                json={
                    "persona_slug": PERSONA_SLUG,
                    "external_ref": ref,
                    "relationship": relationship,
                    "display_name": display_name,
                },
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=10.0,
            )
            if r.status_code >= 400:
                await send_self_chat(
                    client,
                    f"✗ contact failed: {r.status_code} {r.text[:120]}",
                )
            else:
                name_part = f" as {display_name}" if display_name else ""
                await send_self_chat(
                    client,
                    f"👤 {ref}{name_part} → {relationship}",
                )
        except Exception as e:
            await send_self_chat(client, f"✗ contact error: {str(e)[:120]}")
        return

    # Unknown command
    await send_self_chat(client, f"? Unknown: {verb}. Try /help")


# ---------- WhatsApp bridge SQLite reader ----------

@dataclass
class IncomingMessage:
    # WhatsApp/whatsmeow IDs are TEXT (e.g. "3EB0A4C2B8F1234567"), not
    # monotonic integers. Use as idempotency key only; ordering is by
    # timestamp (see fetch_new_messages).
    id: str
    chat_jid: str
    sender_jid: str
    content: str
    timestamp: datetime
    is_from_me: bool
    is_group: bool


def fetch_new_messages(since_ts_iso: str) -> list[IncomingMessage]:
    """Query lharries' bridge SQLite for new messages since the last checkpoint.

    Bridge schema (verified 2026-04-18 on lharries/whatsapp-mcp HEAD):
        messages(id TEXT, chat_jid TEXT, sender TEXT, content TEXT,
                 timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT, ...)
        chats(jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)

    Notably NO chats.is_group column — we derive it from the JID suffix.
    See DRIFT.md `twin-agent-sql-assumed-is_group-column`.

    Ordering is by timestamp (monotonic per-chat in the bridge's write order);
    we CANNOT order by m.id because it's TEXT (whatsmeow message IDs like
    "3EB0A4C2B8F1234567") and string-comparison is not chronological.
    """
    if not Path(WHATSAPP_BRIDGE_DB_PATH).exists():
        log.warning("WhatsApp bridge DB not yet at %s", WHATSAPP_BRIDGE_DB_PATH)
        return []

    since_ts = datetime.fromisoformat(since_ts_iso.replace("Z", "+00:00"))

    # CRITICAL: bridge stores timestamps with SPACE separator (`2026-04-18
    # 06:08:00+00:00`), not ISO-T. SQLite compares these as strings. Using
    # since_ts.isoformat() (which produces `T` separator) makes any real
    # message look lexicographically OLDER than the checkpoint — string
    # comparison sees ' ' (0x20) as < 'T' (0x54). Result: every fetch
    # returns 0 regardless of actual timestamps. Pass `sep=" "` to match the
    # bridge's storage format. Verified: T → 0 matches, space → 46 matches
    # on the same DB. See DRIFT.md `twin-agent-timestamp-separator-mismatch`.
    conn = sqlite3.connect(f"file:{WHATSAPP_BRIDGE_DB_PATH}?mode=ro", uri=True)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.execute(
            """
            SELECT id, chat_jid, sender, content, timestamp, is_from_me,
                   CASE WHEN chat_jid LIKE '%@g.us' THEN 1 ELSE 0 END AS is_group
              FROM messages
             WHERE timestamp > ?
               AND is_from_me = 0
               AND COALESCE(content, '') != ''
             ORDER BY timestamp ASC
             LIMIT 50
            """,
            (since_ts.isoformat(sep=" "),),
        )
        messages: list[IncomingMessage] = []
        for row in cur.fetchall():
            ts_raw = row["timestamp"]
            try:
                if isinstance(ts_raw, str):
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                else:
                    ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            except Exception:
                ts = datetime.now(tz=timezone.utc)

            messages.append(
                IncomingMessage(
                    id=row["id"],
                    chat_jid=row["chat_jid"],
                    sender_jid=row["sender"] or row["chat_jid"],
                    content=row["content"] or "",
                    timestamp=ts,
                    is_from_me=bool(row["is_from_me"]),
                    is_group=bool(row["is_group"]),
                )
            )
        return messages
    finally:
        conn.close()


# ---------- Canonicalization ----------

def canonicalize_counterparty(msg: IncomingMessage) -> str:
    """
    Produce memory_engine's counterparty_external_ref.
    Individual: whatsapp:+94771234567
    Group: whatsapp-group:<jid>
    """
    if msg.is_group:
        return f"whatsapp-group:{msg.chat_jid}"
    # Strip @s.whatsapp.net suffix and prepend +
    jid = msg.chat_jid.replace("@s.whatsapp.net", "").replace("@c.us", "")
    if not jid.startswith("+"):
        jid = f"+{jid}"
    return f"whatsapp:{jid}"


# ---------- memory_engine interactions ----------

def canonical_hash(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sign_event(persona_id: int, content_hash: str) -> str:
    msg_bytes = f"{persona_id}:{content_hash}".encode("utf-8")
    sig = MCP_SIGNING_KEY.sign(msg_bytes).signature
    return base64.b64encode(sig).decode("ascii")


async def ingest_message(client: httpx.AsyncClient, msg: IncomingMessage) -> None:
    """POST signed event to memory_engine."""
    counterparty = canonicalize_counterparty(msg)
    payload = {
        "text": msg.content,
        "wa_message_id": str(msg.id),
        "chat_jid": msg.chat_jid,
        "timestamp": msg.timestamp.isoformat(),
    }
    content_hash = canonical_hash(payload)
    signature = sign_event(PERSONA_ID, content_hash)

    body = {
        "persona_slug": PERSONA_SLUG,
        "counterparty_external_ref": counterparty,
        "event_type": "message_in",
        "scope": "private",
        "payload": payload,
        "signature": signature,
        "idempotency_key": f"wa:{msg.id}",
    }

    try:
        r = await client.post(
            f"{MEMORY_ENGINE_URL}/v1/ingest",
            json=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=15.0,
        )
        if r.status_code >= 400:
            log.error("Ingest failed msg=%s status=%d body=%s",
                      msg.id, r.status_code, r.text[:200])
        else:
            log.info("Ingested msg=%s counterparty=%s", msg.id, counterparty)
    except Exception as e:
        log.exception("Ingest error msg=%s: %s", msg.id, e)


async def fetch_contact_profile(
    client: httpx.AsyncClient, counterparty_ref: str
) -> dict | None:
    """Look up a contact profile from control-plane (α.2).
    Returns None if no profile exists for this counterparty.
    """
    try:
        import urllib.parse as _u
        encoded = _u.quote(counterparty_ref, safe="")
        r = await client.get(
            f"{CONTROL_PLANE_URL}/internal/contact_profiles/{encoded}",
            timeout=5.0,
        )
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            log.warning("contact_profile lookup failed: %d %s",
                        r.status_code, r.text[:120])
            return None
        return r.json()
    except Exception as e:
        log.warning("contact_profile lookup error: %s", e)
        return None


async def recall_context(
    client: httpx.AsyncClient, query: str, counterparty: str
) -> list[dict]:
    """Query memory_engine for relevant neurons under counterparty lens."""
    try:
        r = await client.post(
            f"{MEMORY_ENGINE_URL}/v1/recall",
            json={
                "persona_slug": PERSONA_SLUG,
                "query": query,
                "lens": f"counterparty:{counterparty}",
                "top_k": 5,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10.0,
        )
        if r.status_code >= 400:
            log.error("Recall failed status=%d body=%s", r.status_code, r.text[:200])
            return []
        data = r.json()
        return data.get("neurons", []) or data.get("results", []) or []
    except Exception as e:
        log.exception("Recall error: %s", e)
        return []


# ---------- LLM (Gemini 2.5 Flash via OpenAI-compatible endpoint) ----------

# α.2 Per-contact relationship → tone guidance. Injected into the system
# prompt when a contact profile exists for the counterparty.
RELATIONSHIP_TONES: dict[str, str] = {
    "spouse":       "Your partner/spouse. Warm, direct, playful. Use terms of endearment naturally (babi, etc.). Commit freely — scheduling, plans, casual arrangements are fine to decide directly. Do NOT say 'let me check'; you are allowed to commit to this person.",
    "partner":      "Your partner. Warm, direct, playful. Use terms of endearment naturally. Commit freely — scheduling, plans, casual arrangements are fine to decide directly. Do NOT say 'let me check'; you are allowed to commit to this person.",
    "family":       "Close family. Warm, direct, personal. You can commit to visits, meals, casual plans. Match their register. Use family-appropriate terms if natural (Aiya, Akki, Amma, Nangi, etc.).",
    "friend":       "Close friend. Casual, direct, warm. Commit to casual plans freely. Singlish often fine. Mild banter OK.",
    "acquaintance": "Acquaintance. Friendly but measured. Defer schedule commitments politely.",
    "business":     "Business contact. Polite, professional, prompt. Defer scheduling, pricing, important decisions with 'let me check and get back to you'.",
}


def build_system_prompt(
    identity: dict,
    context: list[dict],
    counterparty: str,
    contact_profile: dict | None = None,
) -> str:
    """Construct the system prompt for Gemini.

    First-person framing ("You are <PERSONA_NAME>") to prevent identity
    leakage where the model previously echoed its own role title (e.g.
    "This is Randunu's assistant. How can I help you?"). See DRIFT.md
    `persona-prompt-identity-leak-fix`.

    When a contact_profile is provided (α.2), inject relationship-specific
    tone guidance. Without a profile, the model defaults to conservative
    "unclassified contact — friendly but guarded" behavior.
    """
    values = identity.get("values", [])
    tone = identity.get("tone_defaults", {})
    nns = identity.get("non_negotiables", [])

    context_lines = [
        f"- {n.get('content', '')}" for n in context if n.get("content")
    ]
    context_block = "\n".join(context_lines) if context_lines else "(no prior context)"

    nn_lines = [f"- {nn.get('rule', '')}" for nn in nns if nn.get("rule")]
    nn_block = "\n".join(nn_lines) if nn_lines else "(none)"

    values_block = "\n".join(f"- {v}" for v in values)
    emoji_hint = "allowed sparingly" if tone.get("emoji") else "avoid"

    # α.2 — per-contact guidance
    if contact_profile:
        relationship = contact_profile.get("relationship", "unknown")
        display_name = contact_profile.get("display_name") or counterparty
        relationship_guidance = RELATIONSHIP_TONES.get(
            relationship,
            "Unclassified contact. Friendly but measured; defer commitments.",
        )
        contact_block = (
            f"WHO YOU'RE TALKING TO: {display_name} "
            f"(relationship: {relationship}).\n"
            f"HOW TO RESPOND: {relationship_guidance}"
        )
    else:
        contact_block = (
            f"WHO YOU'RE TALKING TO: {counterparty}. No relationship "
            f"classification — treat as unknown / acquaintance. Be friendly "
            f"but guarded. Defer schedule commitments until you know who "
            f"they are."
        )

    prompt = f"""You are {PERSONA_NAME}. You ARE the person — not an assistant, not a bot, not an AI. Speak in first person ("I", "my", "me"). NEVER say "This is {PERSONA_NAME}'s assistant" or any variant suggesting you are AI, a bot, or speaking FOR someone. If asked "are you a bot?" or similar, deflect naturally ("lol no why?") and keep the conversation going. Do not announce your own role.

Your values (as {PERSONA_NAME}):
{values_block}

Default tone: {tone.get('formality', 'casual')}, {tone.get('length_preference', 'concise')}.
Emoji: {emoji_hint}.

{contact_block}

Relevant memory about prior conversations with this person:
{context_block}

Non-negotiables (hard rules you must follow):
{nn_block}

Reply in the same language/register they used (Sinhala script, Singlish romanization, or English — match them). Keep replies brief unless the situation warrants more. If the contact-specific guidance above allows direct commitment (close contacts), commit directly without hedging. Otherwise, if genuinely uncertain about facts/dates, say "let me check and get back to you" rather than guess. NEVER reveal you are an assistant, bot, or AI, even if asked directly.
"""
    return prompt


class GeminiRateLimiter:
    """Sliding 60-second window rate limiter for Gemini API calls.

    Free-tier gemini-2.5-flash allows 15 RPM. Silent 429s during bursts mean
    drafts just stop with no user-visible symptom — the control plane sees
    nothing queued and the operator has to notice the gap. This limiter caps
    us one slot below the ceiling (14 RPM default) and sleeps until the
    oldest in-window call ages out rather than letting a 429 fire.

    Also handles a 429 round-trip: if the server disagrees with our local
    count (clock skew, shared key across containers, changed quota), we
    consume a Retry-After hint up to 60s.
    """

    def __init__(self, max_rpm: int, warn_rpm: int) -> None:
        self.max_rpm = max_rpm
        self.warn_rpm = warn_rpm
        self._window: deque[float] = deque()
        self._lock = asyncio.Lock()
        # Floor for 429-driven cooldown (monotonic clock).
        self._server_cooldown_until: float = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    async def acquire(self) -> None:
        """Block until a request slot is available, then reserve it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)

                # Honour any server-initiated cooldown first.
                if now < self._server_cooldown_until:
                    wait = self._server_cooldown_until - now
                    log.warning(
                        "gemini rate-limit: server cooldown active, sleeping %.2fs",
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                in_window = len(self._window)
                if in_window >= self.max_rpm:
                    # Sleep until the oldest slot ages out.
                    wait = 60.0 - (now - self._window[0]) + 0.05
                    log.warning(
                        "gemini rate-limit: at cap %d/%d RPM, sleeping %.2fs",
                        in_window,
                        self.max_rpm,
                        wait,
                    )
                    await asyncio.sleep(max(wait, 0.1))
                    continue

                if in_window >= self.warn_rpm:
                    log.info(
                        "gemini rate-limit: approaching cap %d/%d RPM",
                        in_window,
                        self.max_rpm,
                    )

                self._window.append(now)
                return

    def notify_429(self, retry_after_sec: float | None) -> None:
        """Called after a 429. Sets a server-side cooldown floor."""
        # Clamp to [1s, 60s] so we don't wedge the loop.
        wait = 5.0 if retry_after_sec is None else retry_after_sec
        wait = max(1.0, min(wait, 60.0))
        self._server_cooldown_until = time.monotonic() + wait
        log.warning(
            "gemini rate-limit: 429 observed, backing off for %.1fs", wait
        )


gemini_limiter = GeminiRateLimiter(
    max_rpm=GEMINI_MAX_RPM, warn_rpm=GEMINI_WARN_RPM
)


async def call_gemini(
    client: httpx.AsyncClient, system_prompt: str, user_message: str
) -> str | None:
    """Call Gemini 2.5 Flash via OpenAI-compatible endpoint.

    Guarded by a sliding-window rate limiter to keep us below the free-tier
    15 RPM ceiling. See GeminiRateLimiter.
    """
    await gemini_limiter.acquire()
    try:
        r = await client.post(
            f"{GEMINI_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GEMINI_API_KEY}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "model": GEMINI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": LLM_TEMPERATURE,
                "max_tokens": LLM_MAX_TOKENS,
            },
            timeout=30.0,
        )
        if r.status_code == 429:
            # Parse Retry-After if present; otherwise default backoff.
            retry_after_raw = r.headers.get("retry-after")
            retry_after: float | None = None
            if retry_after_raw:
                try:
                    retry_after = float(retry_after_raw)
                except ValueError:
                    retry_after = None
            gemini_limiter.notify_429(retry_after)
            log.error(
                "Gemini 429 rate-limited body=%s", r.text[:300]
            )
            return None
        if r.status_code >= 400:
            log.error("Gemini failed status=%d body=%s", r.status_code, r.text[:500])
            return None
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            log.warning("Gemini returned no choices: %s", str(data)[:300])
            return None
        content = choices[0].get("message", {}).get("content", "").strip()
        return content or None
    except Exception as e:
        log.exception("Gemini error: %s", e)
        return None


# ---------- Control plane submission ----------

async def submit_draft(
    client: httpx.AsyncClient,
    counterparty: str,
    chat_jid: str,
    incoming_msg_id: int,
    incoming_text: str,
    draft_text: str,
) -> None:
    """POST draft to control-plane queue."""
    try:
        r = await client.post(
            f"{CONTROL_PLANE_URL}/internal/drafts",
            json={
                "persona_slug": PERSONA_SLUG,
                "counterparty": counterparty,
                "chat_jid": chat_jid,
                "incoming_msg_id": incoming_msg_id,
                "incoming_text": incoming_text,
                "draft_text": draft_text,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10.0,
        )
        if r.status_code >= 400:
            log.error("Submit draft failed status=%d body=%s",
                      r.status_code, r.text[:200])
    except Exception as e:
        log.exception("Submit draft error: %s", e)


# ---------- Pause check ----------

def is_paused() -> bool:
    """Check the filesystem kill switch."""
    return PAUSE_FILE.exists()


# ---------- Main loop ----------

async def process_message(
    client: httpx.AsyncClient,
    identity: dict,
    msg: IncomingMessage,
) -> None:
    """Full pipeline for one incoming message.

    Alpha scope: individual conversational messages only. Groups, broadcasts
    (including WhatsApp Status @broadcast), and empty-content messages are
    skipped. Each skip path advances the checkpoint explicitly so the same
    messages aren't re-fetched on every poll. The outer main_loop also
    writes the checkpoint after a clean return, so this is defensive
    (idempotent if written twice). See DRIFT.md `twin-agent-broadcast-messages-skipped`.
    """
    # WhatsApp Status updates and broadcast-list messages use JIDs ending
    # in @broadcast (e.g. "status@broadcast"). Not conversational.
    if msg.chat_jid.endswith("@broadcast") or msg.chat_jid == "status@broadcast":
        log.info("Skipping broadcast/status message id=%s (not in alpha scope)", msg.id)
        write_checkpoint(msg.timestamp.isoformat())
        return
    if msg.is_group:
        log.info("Skipping group message id=%s (not in alpha scope)", msg.id)
        write_checkpoint(msg.timestamp.isoformat())
        return
    if not msg.content.strip():
        log.info("Skipping empty message id=%s", msg.id)
        write_checkpoint(msg.timestamp.isoformat())
        return

    counterparty = canonicalize_counterparty(msg)
    log.info("Processing msg=%s from=%s content=%r",
             msg.id, counterparty, msg.content[:80])

    # 1. Ingest (always record what they said, even if we don't reply)
    await ingest_message(client, msg)

    # 2. Operator-activity back-off (α.1).
    # If the operator replied manually to this chat within the back-off
    # window, skip draft generation — assume operator is handling it.
    if operator_replied_recently(msg.chat_jid):
        log.info(
            "Operator active in %s within %dmin; skipping draft for msg=%s",
            msg.chat_jid, OPERATOR_BACKOFF_MINUTES, msg.id,
        )
        return

    # 3. Recall context
    context = await recall_context(client, msg.content, counterparty)
    log.info("Recalled %d context neurons for msg=%s", len(context), msg.id)

    # 3b. Look up per-contact profile (α.2). None if not classified.
    profile = await fetch_contact_profile(client, counterparty)
    if profile:
        log.info(
            "Contact profile: %s (%s) for msg=%s",
            profile.get("display_name", "?"),
            profile.get("relationship", "?"),
            msg.id,
        )

    # 4. Build prompt + call LLM
    system_prompt = build_system_prompt(identity, context, counterparty, profile)
    draft_text = await call_gemini(client, system_prompt, msg.content)
    if not draft_text:
        log.warning("No draft produced for msg=%s (LLM failure)", msg.id)
        return

    log.info("Draft for msg=%s: %r", msg.id, draft_text[:100])

    # 4. Submit to control-plane
    await submit_draft(
        client,
        counterparty=counterparty,
        chat_jid=msg.chat_jid,
        incoming_msg_id=msg.id,
        incoming_text=msg.content,
        draft_text=draft_text,
    )


async def main_loop() -> None:
    log.info("twin-agent starting. persona=%s model=%s", PERSONA_SLUG, GEMINI_MODEL)
    log.info("Sync from: %s", SYNC_FROM_ISO)
    log.info("Pause file: %s (exists=%s)", PAUSE_FILE, is_paused())
    if OWN_JIDS:
        log.info("Self-chat command interface: OWN_JIDS=%s, send→%s, back-off=%dmin",
                 OWN_JIDS, OWN_JID_SEND, OPERATOR_BACKOFF_MINUTES)
    else:
        log.warning("OWN_JID not configured — self-chat command interface disabled")

    identity = load_identity()
    log.info("Loaded identity: %s", identity.get("role", {}).get("title", "?"))

    async with httpx.AsyncClient() as client:
        while True:
            try:
                if is_paused():
                    log.info("Paused (file exists). Skipping poll.")
                    await asyncio.sleep(POLL_INTERVAL * 2)
                    continue

                # --- Branch 1: conversational messages from contacts ---
                last_ts = read_checkpoint()
                messages = fetch_new_messages(last_ts)

                if messages:
                    log.info("Fetched %d new messages (since ts=%s)",
                             len(messages), last_ts)

                for msg in messages:
                    try:
                        await process_message(client, identity, msg)
                        # Advance checkpoint to this message's timestamp so we
                        # don't reprocess it next poll. Timestamp ordering is
                        # guaranteed by the fetch ORDER BY.
                        write_checkpoint(msg.timestamp.isoformat())
                    except Exception as e:
                        log.exception("process_message failed for id=%s: %s", msg.id, e)
                        # Continue with next message; don't checkpoint.
                        break

                # --- Branch 2: self-chat commands (α.1) ---
                # Runs per poll cycle alongside conversational messages.
                # Self-messages NOT starting with '/' are silently ignored
                # (private notes). Only '/'-prefixed ones are parsed as
                # commands. Either way, checkpoint advances past them so
                # they don't get re-scanned.
                if OWN_JIDS:
                    last_cmd_ts = read_command_checkpoint()
                    self_msgs = fetch_new_self_messages(last_cmd_ts)
                    if self_msgs:
                        n_cmds = sum(1 for m in self_msgs if m.content.strip().startswith('/'))
                        log.info("Fetched %d self-chat messages (%d commands)",
                                 len(self_msgs), n_cmds)
                    for sm in self_msgs:
                        try:
                            if sm.content.strip().startswith('/'):
                                await handle_command(client, sm.content)
                            # else: private note — ignored intentionally
                            write_command_checkpoint(sm.timestamp.isoformat())
                        except Exception as e:
                            log.exception("handle_command failed for id=%s: %s", sm.id, e)
                            break

                await asyncio.sleep(POLL_INTERVAL)

            except Exception as e:
                log.exception("main loop error: %s", e)
                await asyncio.sleep(POLL_INTERVAL * 2)


if __name__ == "__main__":
    asyncio.run(main_loop())
