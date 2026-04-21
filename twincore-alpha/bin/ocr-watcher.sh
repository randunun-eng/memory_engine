#!/bin/bash
# ocr-watcher.sh — 24/7 OCR of new WhatsApp media.
#
# Every OCR_INTERVAL_SEC (default 300s / 5min), runs ocr-backfill.py in
# incremental mode: processes up to OCR_BATCH (default 20) media items
# that don't yet have content. New images sent to the operator's chats
# flow into memory within ~5 min of arrival.
#
# Filters out broadcasts/status, groups, and contacts on a skip-list
# (OCR_SKIP_PREFIXES — comma-separated chat_jid prefixes).
#
# Install as launchd (boot-time):
#   cp twincore-alpha/bin/ai.twincore.ocr-watcher.plist.example \
#      ~/Library/LaunchAgents/ai.twincore.ocr-watcher.plist
#   # edit: replace YOUR_USER with `whoami`
#   launchctl load ~/Library/LaunchAgents/ai.twincore.ocr-watcher.plist
#
# Inspect:
#   tail -f ~/Library/Logs/twincore/ocr-watcher.log

set -euo pipefail

LOG_DIR="${HOME}/Library/Logs/twincore"
mkdir -p "${LOG_DIR}"

ENV_FILE="${ENV_FILE:-${HOME}/Memory_engine/twincore-alpha/.env}"
OCR_SCRIPT="${HOME}/Memory_engine/twincore-alpha/bin/ocr-backfill.py"
OCR_INTERVAL_SEC="${OCR_INTERVAL_SEC:-300}"
OCR_BATCH="${OCR_BATCH:-20}"

# Load secrets from .env (GEMINI_API_KEY, MEMORY_ENGINE_MCP_PRIVATE_KEY).
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
else
  echo "[ocr-watcher] ERROR: ${ENV_FILE} missing" >&2
  exit 1
fi

# Host URLs — launchd runs outside the container network, so always use
# 127.0.0.1 (the docker-compose port mappings) regardless of what .env says.
# .env ships with internal docker DNS names (`memory-engine:4000`) meant
# for container-to-container traffic; those fail to resolve from the host.
export MEMORY_ENGINE_URL="http://127.0.0.1:4000"
export WHATSAPP_BRIDGE_URL="http://127.0.0.1:8080"

# Use the scaffolding repo's uv venv (has all deps + script).
SCAFFOLD_DIR="${HOME}/Memory_engine/files/memory_engine_scaffolding"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

log "ocr-watcher started (interval=${OCR_INTERVAL_SEC}s batch=${OCR_BATCH})"
while true; do
  if ! (cd "${SCAFFOLD_DIR}" && \
        GEMINI_API_KEY="${GEMINI_API_KEY}" \
        MEMORY_ENGINE_MCP_PRIVATE_KEY="${MEMORY_ENGINE_MCP_PRIVATE_KEY}" \
        MEMORY_ENGINE_URL="${MEMORY_ENGINE_URL}" \
        WHATSAPP_BRIDGE_URL="${WHATSAPP_BRIDGE_URL}" \
        uv run python "${OCR_SCRIPT}" --limit "${OCR_BATCH}" 2>&1 | \
        sed -u "s|^|[ocr-tick] |"); then
    log "tick failed (non-fatal); retrying next cycle"
  fi
  sleep "${OCR_INTERVAL_SEC}"
done
