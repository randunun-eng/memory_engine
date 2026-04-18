# TwinCore Alpha

Personal autonomous WhatsApp twin built on:
- **memory_engine** (governed memory layer, Phases 0-6 complete) — https://github.com/randunun-eng/memory_engine
- **lharries/whatsapp-mcp** (WhatsApp Web bridge via whatsmeow) — https://github.com/lharries/whatsapp-mcp
- **Gemini 2.5 Flash** (reasoning, strong Sinhala support)

**Status:** Alpha. Single persona, approval-gated sending, text-only, individual chats only.

---

## What it does

1. Receives WhatsApp messages via lharries' bridge
2. Stores each message as signed event in memory_engine
3. Retrieves relevant prior context under counterparty lens (rule-12 isolation)
4. Drafts a reply using Gemini with identity + context
5. Queues draft for your approval
6. Sends via WhatsApp after you approve

Auto-reply is OFF. Every outbound requires explicit approval.

---

## Prerequisites

- Mac mini (or any Linux/macOS host) with Docker Desktop installed
- Gemini API key from https://aistudio.google.com/apikey (free tier works)
- A WhatsApp account on your phone
- Python 3 with `pynacl` and `pyyaml` installed locally (`pip3 install --user pynacl pyyaml`)

---

## First-run setup

### 1. Copy .env.example → .env and add Gemini key

```bash
cp .env.example .env
# Edit .env, set GEMINI_API_KEY=AIza_YOUR_KEY
```

### 2. Start memory_engine first

```bash
docker compose up -d memory-engine
docker compose logs -f memory-engine
# Wait for "listening on 0.0.0.0:4000"
```

### 3. Run bootstrap

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

This generates all keys, creates the persona, signs identity, registers MCP.
Should print `✅ Bootstrap complete.`

### 4. Optional: test Sinhala end-to-end

```bash
chmod +x test_sinhala.sh
./test_sinhala.sh
```

Should show `✅ Sinhala characters preserved`.

### 5. Start whatsapp-bridge and scan QR code

```bash
docker compose up -d whatsapp-bridge
docker compose logs -f whatsapp-bridge
```

You'll see output containing a QR code URL like `https://quickchart.io/...`.
Open it on any device, then on your phone: **WhatsApp → Settings → Linked Devices → Link a Device**, scan.

Wait until logs show "connected" and "synchronizing history". This may take several minutes for large histories.

### 6. Start twin-agent and control-plane

```bash
docker compose up -d twin-agent control-plane
```

### 7. Test it

From another phone (or ask a friend), send yourself a WhatsApp message.

Within a few seconds, open: http://localhost:4500

You should see the incoming message and a drafted reply. Click **Approve & send** to send.

---

## Operator commands

```bash
# List pending drafts
curl http://localhost:4500/drafts

# Approve/reject a specific draft
curl -X POST http://localhost:4500/drafts/1/approve
curl -X POST http://localhost:4500/drafts/1/reject

# Global halt (no sends regardless)
curl -X POST http://localhost:4500/halt
curl -X POST http://localhost:4500/resume

# Status overview
curl http://localhost:4500/status

# Block/unblock a specific contact
curl -X POST http://localhost:4500/block \
  -H "Content-Type: application/json" \
  -d '{"counterparty":"whatsapp:+94771234567"}'

curl -X POST http://localhost:4500/unblock \
  -H "Content-Type: application/json" \
  -d '{"counterparty":"whatsapp:+94771234567"}'
```

---

## Kill switch (emergency stop)

If the control-plane itself is broken, the filesystem is the source of truth:

```bash
# STOP everything
docker compose exec twin-agent touch /var/twincore/PAUSE
# or directly:
touch ./twincore-state/PAUSE

# RESUME
docker compose exec twin-agent rm /var/twincore/PAUSE
# or:
rm ./twincore-state/PAUSE

# NUCLEAR OPTION
docker compose stop twin-agent
```

---

## Troubleshooting

**QR code doesn't appear in logs.**
Check `docker compose logs whatsapp-bridge` for errors. If it's crashing on startup, likely a CGO/SQLite build issue. Try rebuilding with `docker compose build --no-cache whatsapp-bridge`.

**Drafts never appear.**
Check logs: `docker compose logs -f twin-agent`. Common issues:
- `SYNC_FROM_ISO` in `.env` is in the future. Set it to today or earlier.
- WhatsApp bridge SQLite isn't where we expect it. Check `docker compose exec whatsapp-bridge ls /app/whatsapp-bridge/store/`.
- memory_engine ingest is failing (see its logs).

**Sinhala shows as ??? in drafts.**
Run `./test_sinhala.sh`. If it fails, a component is mishandling UTF-8. Most likely: Gemini not supporting or the content hash mismatch. Check logs.

**Gemini returns 400/403.**
Check `GEMINI_API_KEY` in `.env`. If hitting rate limits (free tier = 15 RPM), wait or upgrade.

**memory_engine schema isn't created.**
Run migrations explicitly:
```bash
docker compose exec memory-engine uv run memory-engine db migrate
```

**CLI commands differ from what bootstrap.sh expects.**
Inspect the actual CLI:
```bash
docker compose exec memory-engine uv run memory-engine --help
```
Adjust bootstrap.sh to match.

---

## What's NOT in this alpha

- Multi-persona routing (single persona only)
- Groups (individual chats only)
- Voice notes, images, videos, documents
- Auto-send (always requires approval)
- Tailscale remote access
- Pretty dashboard (plain HTML only)
- Historical message backfill (only processes messages from `SYNC_FROM_ISO` forward)

---

## What to add next (v2)

After alpha proves out:

1. **Multi-persona routing** — add `randunu_business`, `randunu_family` with a router that picks persona by contact
2. **Auto-reply for specific contacts** — enable for low-stakes contacts only, after observing 50+ approved drafts for that contact
3. **Tailscale remote approval** — approve drafts from phone while away from Mac mini
4. **Voice note transcription** — Whisper + Sinhala
5. **Group chat support** — with rule-12 isolation verified
6. **Historical backfill** — selectively ingest past conversations for long-term context
7. **Real dashboard** — proper web UI, not just curl

---

## Architecture note

This alpha deliberately does not implement what's in memory_engine's Phase 5 WhatsApp adapter. The Phase 5 adapter assumed an event-driven flow (webhook from WhatsApp MCP → memory_engine ingest). lharries' bridge is pull-based (polls SQLite). The twin-agent in this bundle is the reconciliation layer — it polls, then ingests, matching lharries' model while using memory_engine's ingest API. Phase 5's T3/T11 acceptance tests still cover memory_engine's ingest contract; they just run from a different caller now.

---

## Support

Logs for all containers:
```bash
docker compose logs -f
```

Logs for a specific service:
```bash
docker compose logs -f twin-agent
docker compose logs -f memory-engine
docker compose logs -f whatsapp-bridge
docker compose logs -f control-plane
```

Reset state (nuke everything, start over):
```bash
docker compose down -v
rm -rf whatsapp-data whatsapp-auth memory-engine-data control-plane-data twincore-state
```

---

## Security reminders

1. **Never commit `.env`** — it contains your Gemini API key and all generated private keys. `.gitignore` already excludes it.
2. **Rotate any leaked keys immediately** — if you paste a key into chat, a terminal log, or any shared context, treat it as compromised.
3. **Auto-reply stays OFF** until you've observed at least 50 drafts you'd happily have sent manually. Your WhatsApp contacts are real people.
4. **File-based kill switch** is your safety net. Learn the command: `touch ./twincore-state/PAUSE`

---

## License

Personal project. Not licensed for distribution.
