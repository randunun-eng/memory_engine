# Handover to Claude Code

You have received a complete TwinCore Alpha project. All 15 files are already written. Your job is NOT to rewrite them — it is to help the user deploy them successfully.

## What's here

```
twincore-alpha/
├── README.md                       # Setup walkthrough for the user
├── HANDOVER_TO_CLAUDE_CODE.md      # This file — instructions for you
├── docker-compose.yml              # 4-service orchestration
├── .env.example                    # Config template (user copies to .env)
├── .gitignore
├── bootstrap.sh                    # Key generation + persona/MCP registration
├── test_sinhala.sh                 # End-to-end UTF-8 verification
├── whatsapp-bridge/Dockerfile      # Wraps lharries/whatsapp-mcp Go bridge
├── memory_engine_service/Dockerfile # Clones public randunun-eng/memory_engine
├── twin-agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                     # ~400 lines — the core agent
├── control-plane/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                     # ~300 lines — approval + kill switch
└── personas/randunu_primary.yaml   # Starter identity document
```

## Your job

1. **Verify the project structure is intact** (all 15 files present).
2. **Guide the user through first-run setup** following `README.md`.
3. **Troubleshoot specific failures** when they occur.

## Your non-negotiables

- **DO NOT regenerate these files.** They are the spec. If you think one is wrong, ASK the user before changing.
- **DO NOT disable the approval gate.** Auto-reply stays OFF.
- **DO NOT bypass UTF-8 handling.** Sinhala must survive the pipeline.
- **DO NOT hardcode secrets.** Every credential comes from `.env`.
- **DO NOT skip the signing/hash steps** on memory_engine ingest.

## Expected failure points (in likely order)

### 1. whatsapp-bridge build may fail on arm64 Alpine

If `go build` fails with CGO errors:
- Inspect the cloned repo at `/src` in the builder stage
- The Go binary source lives in `whatsapp-bridge/` subdirectory (the repo has both Go bridge AND Python MCP server)
- Fallback: rewrite `whatsapp-bridge/Dockerfile` to use `golang:1.23-bookworm` + `debian:bookworm-slim` instead of Alpine

### 2. memory_engine CLI commands may differ

`bootstrap.sh` and `memory_engine_service/Dockerfile` assume:
- `uv run memory-engine db migrate` — to apply schema
- `uv run memory-engine serve` — to start HTTP server

If these fail, inspect actual CLI:
```bash
docker compose exec memory-engine uv run memory-engine --help
```
Adjust both files to match.

### 3. memory_engine HTTP routes may differ

`bootstrap.sh` POSTs to:
- `/v1/personas` (create persona)
- `/v1/mcp/register` (register MCP signing key)
- `/v1/identity/load` (load signed identity YAML)

`twin-agent/main.py` POSTs to:
- `/v1/ingest` (signed event)
- `/v1/recall` (query under lens)

If any return 404, inspect actual routes:
```bash
docker compose exec memory-engine uv run python -c "
from memory_engine.http.app import app
for r in app.routes:
    print(r.path if hasattr(r, 'path') else r)
"
```

Report mismatches to the user BEFORE editing. Do not guess.

### 4. whatsapp-bridge SQLite schema may differ

`twin-agent/main.py` assumes:
- Table `messages` with columns: `id, chat_jid, sender, content, timestamp, is_from_me`
- Table `chats` with columns: `jid, is_group`

Inspect actual schema:
```bash
docker compose exec whatsapp-bridge sqlite3 /app/whatsapp-bridge/store/messages.db ".schema"
```

Adjust the SQL in `fetch_new_messages()` if needed.

### 5. whatsapp-bridge send endpoint may differ

`control-plane/main.py` POSTs approved drafts to `$WHATSAPP_BRIDGE_URL/api/send` with body `{recipient, message}`.

Verify this is the actual endpoint. If it differs, check lharries' bridge documentation or source.

### 6. sqlite-vec may fail to install on arm64

If memory_engine Dockerfile fails on `pip install sqlite-vec`:
```bash
# Build from source
RUN git clone https://github.com/asg017/sqlite-vec /tmp/sqlite-vec \
 && cd /tmp/sqlite-vec && make loadable \
 && cp dist/vec0.so /usr/local/lib/
```

### 7. Gemini may refuse Sinhala with safety filters

If Gemini returns refusals for Sinhala content, adjust the OpenAI-compatible endpoint call in `twin-agent/main.py` to include safety_settings. Or switch to native Gemini endpoint.

## First-run sequence (guide the user through this)

```bash
# 1. User creates .env from template
cp .env.example .env
# User edits .env and adds their GEMINI_API_KEY

# 2. Build all images
docker compose build

# 3. Start memory_engine first (for bootstrap)
docker compose up -d memory-engine
docker compose logs -f memory-engine
# Wait for "listening on 0.0.0.0:4000"
# Ctrl+C to exit logs

# 4. Run bootstrap (generates keys, creates persona, signs identity)
chmod +x bootstrap.sh test_sinhala.sh
./bootstrap.sh

# 5. Optional: verify Sinhala end-to-end
./test_sinhala.sh

# 6. Start whatsapp-bridge and show QR code
docker compose up -d whatsapp-bridge
docker compose logs -f whatsapp-bridge
# User scans QR with phone
# Wait for "connected" and "synchronizing history"
# Ctrl+C to exit logs

# 7. Start twin-agent + control-plane
docker compose up -d twin-agent control-plane

# 8. Verify
curl http://localhost:4500/status
# Open http://localhost:4500 in browser

# 9. Test
# User sends themselves a WhatsApp message from another phone.
# Draft appears at http://localhost:4500 within ~5 seconds.
# User clicks "Approve & send" or:
curl -X POST http://localhost:4500/drafts/1/approve
```

## If something fails irrecoverably

Report to the user with:
1. What you tried
2. Exact error message
3. What you think the fix requires
4. Whether it's a spec bug (to report back) or environment issue

Do not improvise fixes that compromise architecture.

## Kill switch priority

The user may panic if drafts start going wrong. Make sure they know:

```bash
# Nuclear option
docker compose stop twin-agent

# File-based kill switch (stops all sends)
touch ./twincore-state/PAUSE

# Resume
rm ./twincore-state/PAUSE

# Block specific contact
curl -X POST http://localhost:4500/block \
  -H "Content-Type: application/json" \
  -d '{"counterparty":"whatsapp:+94771234567"}'
```

## End of handover
