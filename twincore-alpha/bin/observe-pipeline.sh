#!/bin/bash
# observe-pipeline.sh — 24/7 WhatsApp pipeline observation.
#
# Every 60s, scans three live DBs and appends one JSON object per row to
# ~/Library/Logs/twincore/pipeline-trace.jsonl. Uses sqlite3's native JSON
# mode to tolerate multi-line message content, embedded quotes, emoji, etc.
#
# Sources:
#   bridge/messages.db        -> inbound + operator direct replies
#   control-plane/control.db  -> drafts (pending/sent/rejected) + status
#   memory-engine/engine.db   -> aggregate snapshot (events/neurons/quarantine)
#
# Review:
#   bin/observe-summary.sh 24h

set -euo pipefail

LOG_DIR="${HOME}/Library/Logs/twincore"
TRACE_FILE="${LOG_DIR}/pipeline-trace.jsonl"
STATE_FILE="${LOG_DIR}/observer-state.json"
mkdir -p "${LOG_DIR}"

BRIDGE_DB="${BRIDGE_DB:-${HOME}/Memory_engine/twincore-alpha/whatsapp-data/messages.db}"
CP_VOLUME="${CP_VOLUME:-twincore-alpha_control-plane-data}"
ME_VOLUME="${ME_VOLUME:-twincore-alpha_memory-engine-data}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >&2; }

# Query a docker-volume sqlite DB. Returns one JSON array on stdout.
query_volume_json() {
  local vol=$1 db=$2 sql=$3
  docker run --rm -v "${vol}:/v" alpine sh -c "
    apk add -q sqlite >/dev/null 2>&1
    sqlite3 -json /v/${db} \"${sql}\"
  " 2>/dev/null || echo '[]'
}

if [[ ! -f "${STATE_FILE}" ]]; then
  jq -nc '{last_bridge_ts:"2026-04-19 00:00:00+00:00", last_draft_id:0}' > "${STATE_FILE}"
  log "initialized state at ${STATE_FILE}"
fi

poll_once() {
  local state last_bridge_ts last_draft_id
  state=$(cat "${STATE_FILE}")
  last_bridge_ts=$(jq -r '.last_bridge_ts' <<< "${state}")
  last_draft_id=$(jq -r '.last_draft_id' <<< "${state}")
  local new_bridge_ts="${last_bridge_ts}"
  local new_draft_id="${last_draft_id}"
  local poll_ts
  poll_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # ---- 1. Bridge messages (inbound + operator outbound) ----
  local bridge_rows
  bridge_rows=$(sqlite3 -json "${BRIDGE_DB}" "
    SELECT id, chat_jid, sender, content, timestamp, is_from_me
    FROM messages
    WHERE timestamp > '${last_bridge_ts}'
      AND COALESCE(content,'') != ''
    ORDER BY timestamp ASC
    LIMIT 200;")
  if [[ -n "${bridge_rows}" && "${bridge_rows}" != "[]" ]]; then
    echo "${bridge_rows}" | jq -c --arg ts "${poll_ts}" '
      .[] | {
        ts: $ts,
        kind: (if .is_from_me == 1 then "outbound" else "inbound" end),
        id, chat_jid, sender,
        text: (.content // ""),
        msg_ts: .timestamp
      }' >> "${TRACE_FILE}"
    new_bridge_ts=$(echo "${bridge_rows}" | jq -r '.[-1].timestamp')
  fi

  # ---- 2. Drafts ----
  local draft_rows
  draft_rows=$(query_volume_json "${CP_VOLUME}" control.db "
    SELECT id, persona_slug, counterparty, incoming_msg_id,
           incoming_text, draft_text, status,
           created_at, decided_at, sent_at, error
    FROM drafts
    WHERE id > ${last_draft_id}
    ORDER BY id ASC
    LIMIT 200;")
  if [[ -n "${draft_rows}" && "${draft_rows}" != "[]" ]]; then
    echo "${draft_rows}" | jq -c --arg ts "${poll_ts}" '
      .[] | . + {ts: $ts, kind: "draft"}' >> "${TRACE_FILE}"
    new_draft_id=$(echo "${draft_rows}" | jq -r '.[-1].id')
  fi

  # ---- 3. Engine snapshot ----
  local eng_rows
  eng_rows=$(query_volume_json "${ME_VOLUME}" engine.db "
    SELECT
      (SELECT COUNT(*) FROM events) AS events,
      (SELECT COUNT(*) FROM neurons WHERE superseded_at IS NULL) AS active_neurons,
      (SELECT COUNT(*) FROM quarantine_neurons WHERE reviewed_at IS NULL) AS pending_quarantine,
      (SELECT COUNT(*) FROM consolidation_log) AS consolidation_log
    ;")
  if [[ -n "${eng_rows}" && "${eng_rows}" != "[]" ]]; then
    echo "${eng_rows}" | jq -c --arg ts "${poll_ts}" '
      .[0] + {ts: $ts, kind: "snapshot"}' >> "${TRACE_FILE}"
  fi

  # ---- Persist state ----
  jq -nc \
    --arg ts "${new_bridge_ts}" \
    --argjson did "${new_draft_id}" \
    '{last_bridge_ts: $ts, last_draft_id: $did}' \
    > "${STATE_FILE}"
}

log "observer starting; log=${TRACE_FILE}"
while true; do
  if ! poll_once; then
    log "poll failed (non-fatal); retrying next cycle"
  fi
  sleep 60
done
