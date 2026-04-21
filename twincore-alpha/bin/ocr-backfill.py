#!/usr/bin/env python3
"""Download + OCR WhatsApp media via Gemini vision, backfill content.

For each message with media_type in (image, video) and empty content:
  1. POST /api/download to bridge — get decrypted file on host disk.
  2. Read bytes, send to gemini-2.5-flash with vision prompt.
  3. Write the returned description back into messages.content.
  4. Trigger memory_engine /v1/ingest so the new text flows into
     recall + grounding.

Run once (or periodically via launchd) after rebuilding memory-engine
so the new content gets picked up as a fresh consolidation event.

Usage:
  GEMINI_API_KEY=... uv run python ocr-backfill.py [--chat JID] [--limit N]
  GEMINI_API_KEY=... uv run python ocr-backfill.py --chat 94777319573@s.whatsapp.net --limit 20

Skips:
  - messages with content already set (already OCR'd or text-only)
  - broadcast/status messages
  - very large files (> 20 MB)

Cost estimate: ~$0.0002 per image on Gemini Flash free tier; free
under 15 RPM / 1.5K RPD.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx
from nacl.signing import SigningKey

BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:8080")
MEMORY_ENGINE_URL = os.environ.get("MEMORY_ENGINE_URL", "http://127.0.0.1:4000")
PERSONA_SLUG = os.environ.get("PERSONA_SLUG", "randunu_primary")
BRIDGE_DB = Path(
    os.environ.get(
        "BRIDGE_DB",
        str(Path.home() / "Memory_engine/twincore-alpha/whatsapp-data/messages.db"),
    )
)
MEDIA_ROOT = BRIDGE_DB.parent  # /whatsapp-data/<chat_jid>/<filename>
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
)

# Sign media_ocr events with the MCP private key — memory-engine's ingest
# route verifies signatures against the registered MCP public key stored
# in the mcp_sources table, NOT against the persona owner key. Persona
# owner key signs identity docs, MCP key signs events.
MCP_PRIVATE_KEY_B64 = os.environ.get("MEMORY_ENGINE_MCP_PRIVATE_KEY", "")

OCR_PROMPT = """You are describing a WhatsApp media message for a personal
memory system. Extract every piece of information a human would need to
recall what the message communicated:

- ALL visible text verbatim (prices, product names, codes, dates, numbers,
  hand-written notes) in the original language (Sinhala / Singlish /
  English — preserve script).
- Brief physical context (what's in the image: a screenshot, a
  whiteboard, a product, a circuit board, a meme, a screenshot of a
  chat, etc.).
- If it's a meme or sticker, note the humour/message.
- If it's a video, describe the first frame only.

Output 1-3 sentences. No lists, no markdown. Plain prose in ENGLISH
(translate Sinhala/Singlish text to English inline, but keep product
codes / hashtags / numbers / names verbatim). Be specific, not generic."""


def download_media(
    client: httpx.Client, message_id: str, chat_jid: str
) -> Path | None:
    """Call bridge /api/download; return host path of decrypted file."""
    try:
        r = client.post(
            f"{BRIDGE_URL}/api/download",
            json={"message_id": message_id, "chat_jid": chat_jid},
            timeout=30.0,
        )
    except Exception as e:
        print(f"  [skip] bridge download failed: {e}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"  [skip] bridge {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    data = r.json()
    if not data.get("success"):
        print(f"  [skip] bridge: {data.get('message')}", file=sys.stderr)
        return None
    # bridge path is /app/whatsapp-bridge/store/<chat>/<file>
    # host mount is $(pwd)/whatsapp-data -> /app/whatsapp-bridge/store
    filename = data["filename"]
    host_path = MEDIA_ROOT / chat_jid / filename
    if not host_path.exists():
        print(f"  [skip] host path not found: {host_path}", file=sys.stderr)
        return None
    return host_path


def ocr_with_gemini(client: httpx.Client, img_path: Path) -> str | None:
    """Send image to Gemini vision, return descriptive text."""
    raw = img_path.read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        print(f"  [skip] too large ({len(raw)} bytes)", file=sys.stderr)
        return None
    b64 = base64.b64encode(raw).decode()
    mime = "image/jpeg" if img_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    body = {
        "model": "gemini-2.5-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.0,
    }
    try:
        r = client.post(
            GEMINI_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {GEMINI_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
    except Exception as e:
        print(f"  [skip] gemini error: {e}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"  [skip] gemini {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    choices = r.json().get("choices") or []
    if not choices:
        return None
    text = (choices[0].get("message") or {}).get("content", "").strip()
    return text or None


def _compute_content_hash(payload: dict) -> str:
    """Mirror memory_engine.core.events.compute_content_hash."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_signing_message(persona_id: int, content_hash: str) -> bytes:
    """Mirror memory_engine.policy.signing.canonical_signing_message."""
    return f"{persona_id}:{content_hash}".encode()


def _canonical_counterparty(chat_jid: str) -> str:
    """Map bridge chat_jid -> memory-engine counterparty external_ref.
    Matches the shape twin-agent uses in ingest.
    """
    # Strip WhatsApp suffix; map to whatsapp:+<digits>[@lid]
    if "@" not in chat_jid:
        return f"whatsapp:+{chat_jid}"
    digits, suffix = chat_jid.split("@", 1)
    if suffix == "s.whatsapp.net":
        return f"whatsapp:+{digits}"
    return f"whatsapp:+{digits}@{suffix}"


def post_media_ocr_event(
    client: httpx.Client,
    persona_id: int,
    signer: SigningKey,
    bridge_msg_id: str,
    chat_jid: str,
    media_type: str,
    ocr_text: str,
    bridge_timestamp: str,
) -> bool:
    """Append a media_ocr event to memory_engine via /v1/ingest.

    Separate from the original (empty-content) ingest event: cites the
    original msg_id in the payload, uses idempotency_key f"{id}-ocr".
    On next consolidation tick the extractor sees it + the context
    improves.
    """
    counterparty = _canonical_counterparty(chat_jid)
    payload = {
        "text": ocr_text,
        "media_ocr": True,
        "media_type": media_type,
        "original_msg_id": bridge_msg_id,
        "bridge_timestamp": bridge_timestamp,
    }
    content_hash = _compute_content_hash(payload)
    sig = signer.sign(_canonical_signing_message(persona_id, content_hash)).signature
    sig_b64 = base64.b64encode(sig).decode()

    body = {
        "persona_slug": PERSONA_SLUG,
        "counterparty_external_ref": counterparty,
        "event_type": "message_in",  # treated like an inbound so consolidator processes it
        "scope": "private",
        "payload": payload,
        "signature": sig_b64,
        "idempotency_key": f"{bridge_msg_id}-ocr",
    }
    try:
        r = client.post(
            f"{MEMORY_ENGINE_URL}/v1/ingest",
            json=body,
            timeout=15.0,
        )
    except Exception as e:
        print(f"  [ingest-skip] {e}", file=sys.stderr)
        return False
    if r.status_code == 409:
        print("  [ingest-skip] already ingested (idempotency)", file=sys.stderr)
        return False
    if r.status_code >= 400:
        print(f"  [ingest-skip] {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", help="Filter to one chat_jid")
    ap.add_argument("--limit", type=int, default=20, help="Max images to process")
    ap.add_argument(
        "--dry-run", action="store_true", help="Download + OCR but don't write to DB"
    )
    args = ap.parse_args()

    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        return 1
    if not MCP_PRIVATE_KEY_B64:
        print("ERROR: MEMORY_ENGINE_MCP_PRIVATE_KEY not set", file=sys.stderr)
        return 1

    signer = SigningKey(base64.b64decode(MCP_PRIVATE_KEY_B64))

    # Look up persona_id (memory-engine expects numeric id in signing message)
    http_init = httpx.Client()
    try:
        r = http_init.post(
            f"{MEMORY_ENGINE_URL}/v1/recall",
            json={"persona_slug": PERSONA_SLUG, "query": "_", "lens": "auto", "top_k": 1},
            timeout=10.0,
        )
        if r.status_code == 404:
            print(f"ERROR: persona '{PERSONA_SLUG}' not found in memory-engine", file=sys.stderr)
            return 1
    finally:
        http_init.close()
    persona_id = 1  # alpha is single-persona; hardcoded. TODO: /v1/personas/{slug} lookup

    conn = sqlite3.connect(str(BRIDGE_DB))
    conn.row_factory = sqlite3.Row
    where = [
        "media_type IN ('image', 'video')",
        "COALESCE(content, '') = ''",
        "chat_jid NOT LIKE '%broadcast%'",
        "chat_jid NOT LIKE '%status%'",
    ]
    params: list[object] = []
    if args.chat:
        where.append("chat_jid = ?")
        params.append(args.chat)
    params.append(args.limit)
    rows = conn.execute(
        f"SELECT id, chat_jid, media_type, timestamp "
        f"FROM messages WHERE {' AND '.join(where)} "
        f"ORDER BY timestamp DESC LIMIT ?",
        params,
    ).fetchall()
    if not rows:
        print("No media to process.", file=sys.stderr)
        return 0

    print(
        f"Processing {len(rows)} media messages "
        f"(chat={args.chat or 'ALL'}, dry_run={args.dry_run})"
    )

    http = httpx.Client()
    ok = 0
    skip = 0
    for row in rows:
        print(
            f"\n[{row['timestamp']}] {row['chat_jid'][:40]} "
            f"{row['media_type']} id={row['id']}"
        )
        path = download_media(http, row["id"], row["chat_jid"])
        if path is None:
            skip += 1
            continue
        print(f"  → downloaded: {path.name} ({path.stat().st_size} bytes)")

        if row["media_type"] == "video":
            # TODO: frame-extract with ffmpeg; for MVP treat as placeholder
            description = "[video — frame extraction not yet implemented]"
        else:
            description = ocr_with_gemini(http, path)
            if not description:
                skip += 1
                continue
            print(f"  → ocr: {description[:120]}")

        if not args.dry_run:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ? AND chat_jid = ?",
                (description, row["id"], row["chat_jid"]),
            )
            conn.commit()
            # Append media_ocr event to memory-engine so consolidator
            # picks it up on the next tick.
            ingest_ok = post_media_ocr_event(
                http,
                persona_id=persona_id,
                signer=signer,
                bridge_msg_id=row["id"],
                chat_jid=row["chat_jid"],
                media_type=row["media_type"],
                ocr_text=description,
                bridge_timestamp=row["timestamp"],
            )
            if ingest_ok:
                print("  → ingested to memory-engine")
        ok += 1
        # Be polite to Gemini's 15 RPM free tier
        time.sleep(4.5)

    print(f"\nDone. processed={ok} skipped={skip}")
    http.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
