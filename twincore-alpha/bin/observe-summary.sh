#!/bin/bash
# observe-summary.sh — read pipeline-trace.jsonl and print a human-readable
# report of (inbound → draft → operator action) tuples.

set -euo pipefail

LOG_DIR="${HOME}/Library/Logs/twincore"
TRACE_FILE="${LOG_DIR}/pipeline-trace.jsonl"

if [[ ! -f "${TRACE_FILE}" ]]; then
  echo "No trace file at ${TRACE_FILE}. Run bin/observe-pipeline.sh first." >&2
  exit 1
fi

SINCE="${1:-24h}"

case "${SINCE}" in
  *h) HOURS=${SINCE%h}; CUTOFF=$(date -u -v-${HOURS}H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "${HOURS} hours ago" +%Y-%m-%dT%H:%M:%SZ) ;;
  *d) DAYS=${SINCE%d}; CUTOFF=$(date -u -v-${DAYS}d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "${DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ) ;;
  *)  CUTOFF="${SINCE}" ;;
esac

echo "Pipeline trace summary — since ${CUTOFF}"
echo "======================================================================"

TOTAL_INBOUND=$(jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="inbound" and .ts>=$cut)] | length' "${TRACE_FILE}")
TOTAL_OUTBOUND=$(jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="outbound" and .ts>=$cut)] | length' "${TRACE_FILE}")
TOTAL_DRAFTS=$(jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="draft" and .ts>=$cut)] | length' "${TRACE_FILE}")
DRAFTS_SENT=$(jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="draft" and .ts>=$cut and .status=="sent")] | length' "${TRACE_FILE}")
DRAFTS_REJECTED=$(jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="draft" and .ts>=$cut and .status=="rejected")] | length' "${TRACE_FILE}")
DRAFTS_PENDING=$(jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="draft" and .ts>=$cut and .status=="pending")] | length' "${TRACE_FILE}")

echo "Inbound messages:   ${TOTAL_INBOUND}"
echo "Outbound (you):     ${TOTAL_OUTBOUND}"
echo "Drafts produced:    ${TOTAL_DRAFTS}"
echo "  |- sent:         ${DRAFTS_SENT}"
echo "  |- rejected:     ${DRAFTS_REJECTED}"
echo "  |- pending:      ${DRAFTS_PENDING}"
if [[ "${TOTAL_INBOUND}" -gt 0 ]]; then
  PCT=$(awk -v d="${TOTAL_DRAFTS}" -v i="${TOTAL_INBOUND}" 'BEGIN {printf "%.0f", (d/i)*100}')
  echo "Draft coverage:     ${PCT}% of inbound produced a draft"
fi
if [[ "${TOTAL_DRAFTS}" -gt 0 ]]; then
  PCT=$(awk -v s="${DRAFTS_SENT}" -v d="${TOTAL_DRAFTS}" 'BEGIN {printf "%.0f", (s/d)*100}')
  echo "Approval rate:      ${PCT}% of drafts sent"
fi
echo

echo "Latest snapshot:"
jq -s --arg cut "${CUTOFF}" '[.[] | select(.kind=="snapshot" and .ts>=$cut)] | last // {}' "${TRACE_FILE}"
echo

echo "Recent (inbound -> draft -> operator action) tuples:"
echo "----------------------------------------------------------------------"

jq -s -r --arg cut "${CUTOFF}" '
  # extract the numeric phone/LID from either format
  # "whatsapp:+94777319573" -> "94777319573"
  # "94777319573@s.whatsapp.net" -> "94777319573"
  # "whatsapp:+209376166060226@lid" -> "209376166060226"
  def phone: sub("^whatsapp:\\+"; "") | split("@")[0];

  . as $all
  | [$all[] | select(.kind=="draft" and .ts>=$cut)] as $drafts
  | $drafts[-20:][]
  | . as $d
  | ($d.counterparty | phone) as $dph
  | ($all | map(select(
      .kind=="outbound"
      and ((.chat_jid // "") | phone) == $dph
      and (.msg_ts // "") >= ($d.created_at // "")
    )) | first) as $op
  | "\nINBOUND:  [" + ($d.counterparty // "?") + "] " + (($d.incoming_text // "")[0:120] // "")
    + "\nDRAFT:    (" + ($d.status // "?") + ") " + (($d.draft_text // "")[0:160])
    + "\nYOU:      " + (($op.text // "(no outbound recorded)")[0:160])
    + "\n---"
' "${TRACE_FILE}" 2>&1 | head -180
