#!/bin/bash
# observe-contact.sh — focused view of a single contact's pipeline.
#
# Usage: observe-contact.sh <incoming_jid> [outgoing_lid] [since_hours]
#
# Example:
#   observe-contact.sh 94777319573@s.whatsapp.net 71301255459057@lid 48
#
# Shows:
#   - Every message (inbound from contact, outbound from you) chronologically
#   - Every draft the twin produced for this contact
#   - Media placeholders (bridge doesn't OCR — see twincore-alpha/DRIFT.md
#     `bridge-images-not-downloaded-or-ocr'd`)
#   - Approval rate + typical length mismatch
#
# Queries the bridge DB directly (host-mounted) + pulls drafts via the
# control-plane DB in its named docker volume.

set -euo pipefail

IN_JID="${1:-}"
OUT_LID="${2:-}"
SINCE_H="${3:-48}"

if [[ -z "${IN_JID}" ]]; then
  echo "Usage: $0 <incoming_jid> [outgoing_lid] [since_hours]" >&2
  exit 1
fi

BRIDGE_DB="${BRIDGE_DB:-${HOME}/Memory_engine/twincore-alpha/whatsapp-data/messages.db}"
CP_VOLUME="${CP_VOLUME:-twincore-alpha_control-plane-data}"

# Cutoff for SQL timestamp filter (space-separator, bridge format)
CUTOFF=$(date -u -v-${SINCE_H}H +'%Y-%m-%d %H:%M:%S+00:00' 2>/dev/null \
      || date -u -d "${SINCE_H} hours ago" +'%Y-%m-%d %H:%M:%S+00:00')

echo "Contact view — in=${IN_JID} out=${OUT_LID:-n/a} since=${CUTOFF}"
echo "======================================================================"

# Build chronological message list. Include BOTH JIDs if outgoing LID given.
JID_LIST="'${IN_JID}'"
if [[ -n "${OUT_LID}" ]]; then
  JID_LIST="${JID_LIST}, '${OUT_LID}'"
fi

echo "--- Messages (chronological) ---"
sqlite3 "${BRIDGE_DB}" -separator $'\t' "
  SELECT timestamp,
         CASE is_from_me WHEN 1 THEN 'YOU ' ELSE '<-  ' END,
         CASE WHEN media_type IS NOT NULL AND media_type != ''
              THEN '[' || media_type || ' — no OCR]'
              ELSE substr(COALESCE(content,''),1,200) END
  FROM messages
  WHERE chat_jid IN (${JID_LIST})
    AND timestamp >= '${CUTOFF}'
  ORDER BY timestamp ASC;
" 2>/dev/null | while IFS=$'\t' read -r ts dir body; do
  echo "${ts}  ${dir}${body}"
done

echo
echo "--- Drafts produced for this contact ---"
# Derive phone-part for counterparty matching.
# IN_JID could be `94777319573@s.whatsapp.net` -> `94777319573` (in drafts as `whatsapp:+94777319573`)
# OUT_LID is the operator's reply side, so drafts are against IN_JID normally.
PHONE_PART="${IN_JID%%@*}"
LID_PART=""
if [[ -n "${OUT_LID}" ]]; then
  LID_PART="${OUT_LID%%@*}"
fi

docker run --rm -v "${CP_VOLUME}:/v" alpine sh -c "
  apk add -q sqlite >/dev/null 2>&1
  sqlite3 -separator \$'\t' /v/control.db \"
    SELECT id, status, created_at, substr(incoming_text,1,80), substr(draft_text,1,160)
    FROM drafts
    WHERE counterparty LIKE '%${PHONE_PART}%' ${LID_PART:+OR counterparty LIKE \'%${LID_PART}%\'}
      AND created_at >= '${CUTOFF}'
    ORDER BY created_at ASC;
  \"
" 2>/dev/null | while IFS=$'\t' read -r id status ts inbound draft; do
  echo "[#${id} ${status} ${ts}]"
  echo "  they: ${inbound}"
  echo "  twin: ${draft}"
  echo
done

echo "--- Aggregate ---"
MSG_IN=$(sqlite3 "${BRIDGE_DB}" "SELECT COUNT(*) FROM messages WHERE chat_jid IN (${JID_LIST}) AND is_from_me=0 AND COALESCE(content,'')!='' AND timestamp >= '${CUTOFF}';" 2>/dev/null)
MSG_OUT=$(sqlite3 "${BRIDGE_DB}" "SELECT COUNT(*) FROM messages WHERE chat_jid IN (${JID_LIST}) AND is_from_me=1 AND COALESCE(content,'')!='' AND timestamp >= '${CUTOFF}';" 2>/dev/null)
MEDIA=$(sqlite3 "${BRIDGE_DB}" "SELECT COUNT(*) FROM messages WHERE chat_jid IN (${JID_LIST}) AND media_type IS NOT NULL AND media_type != '' AND timestamp >= '${CUTOFF}';" 2>/dev/null)
echo "inbound text:  ${MSG_IN}"
echo "outbound text: ${MSG_OUT}"
echo "media (no OCR): ${MEDIA}"
