#!/usr/bin/env bash
#
# twincore-alpha whatsmeow drift monitor (P0 #3).
#
# Every week:
#   1. Read the pinned whatsmeow commit from whatsapp-bridge/Dockerfile.
#   2. Ask the GitHub API how far HEAD of tulir/whatsmeow has moved past it.
#   3. If HEAD is ahead, drop a self-chat WhatsApp alert so the operator
#      sees it on their phone, not buried in a log file.
#
# Why this matters:
#   whatsmeow's waVersion string goes stale roughly monthly. When it
#   does, the bridge can't complete the websocket handshake and the
#   whole twin goes silent. The symptom is HTTP 405 on connect — by
#   which point the twin has already been down for hours. We want to
#   catch the drift before WhatsApp rotates the floor, not after.
#
# Usage:
#   bin/check-whatsmeow-drift.sh
#
# Environment:
#   TWINCORE_ROOT          live deployment path (default: script's parent)
#   GITHUB_TOKEN           optional, lifts the 60 req/hr anon rate limit
#   DRIFT_ALERT_THRESHOLD  commits ahead before we alert (default: 1)
#   BRIDGE_URL             whatsapp-bridge HTTP (default: localhost:8080)
#   OWN_JID_SEND           self-chat target JID (read from .env if unset)
#
# Schedule: ~/Library/LaunchAgents/ai.twincore.whatsmeow-drift.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TWINCORE_ROOT="${TWINCORE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DOCKERFILE="$TWINCORE_ROOT/whatsapp-bridge/Dockerfile"
DRIFT_ALERT_THRESHOLD="${DRIFT_ALERT_THRESHOLD:-1}"
BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8080}"

if [[ ! -f "$DOCKERFILE" ]]; then
    echo "ERROR: whatsapp-bridge/Dockerfile not found at $DOCKERFILE" >&2
    exit 2
fi

# Source .env for OWN_JID if not provided.
if [[ -z "${OWN_JID_SEND:-}" && -f "$TWINCORE_ROOT/.env" ]]; then
    OWN_JID_RAW=$(grep -E "^OWN_JID=" "$TWINCORE_ROOT/.env" | head -1 | cut -d= -f2-)
    OWN_JID_SEND="${OWN_JID_RAW%%,*}"
fi

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# --- 1. Extract pinned SHA ---
# The Dockerfile line is:
#   RUN go get go.mau.fi/whatsmeow@<40-char-sha> && go mod tidy
PINNED=$(grep -oE "whatsmeow@[0-9a-f]{40}" "$DOCKERFILE" | head -1 | cut -d@ -f2)
if [[ -z "$PINNED" ]]; then
    echo "ERROR: could not parse pinned whatsmeow SHA from $DOCKERFILE" >&2
    exit 2
fi
log "pinned: $PINNED"

# --- 2. Query GitHub ---
GH_AUTH=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    GH_AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi

# HEAD of default branch. Use ${var[@]+"${var[@]}"} idiom so empty array is
# safe under `set -u` on macOS bash 3.2.
HEAD_JSON=$(curl -fsSL ${GH_AUTH[@]+"${GH_AUTH[@]}"} \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/tulir/whatsmeow/commits/HEAD")
HEAD_SHA=$(printf '%s' "$HEAD_JSON" | grep -oE '"sha": *"[0-9a-f]{40}"' | head -1 | grep -oE '[0-9a-f]{40}')
HEAD_DATE=$(printf '%s' "$HEAD_JSON" | grep -oE '"date": *"[^"]+"' | head -1 | cut -d'"' -f4)

if [[ -z "$HEAD_SHA" ]]; then
    echo "ERROR: could not parse HEAD sha from GitHub response" >&2
    exit 1
fi
log "HEAD:   $HEAD_SHA (committed $HEAD_DATE)"

# If the pin IS HEAD, we're done — no drift.
if [[ "$PINNED" == "$HEAD_SHA" ]]; then
    log "no drift — pin is current HEAD"
    exit 0
fi

# --- 3. Compare ---
COMPARE=$(curl -fsSL ${GH_AUTH[@]+"${GH_AUTH[@]}"} \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/tulir/whatsmeow/compare/${PINNED}...${HEAD_SHA}")
AHEAD_BY=$(printf '%s' "$COMPARE" | grep -oE '"ahead_by": *[0-9]+' | head -1 | grep -oE '[0-9]+')
AHEAD_BY="${AHEAD_BY:-0}"
log "drift:  HEAD is $AHEAD_BY commits ahead of pin"

if (( AHEAD_BY < DRIFT_ALERT_THRESHOLD )); then
    log "below alert threshold ($DRIFT_ALERT_THRESHOLD) — no notification"
    exit 0
fi

# --- 4. Alert via self-chat ---
MSG="[twincore] whatsmeow pin drift: $AHEAD_BY commits behind HEAD ($HEAD_SHA, $HEAD_DATE). Check store/clientpayload.go for waVersion bump and update whatsapp-bridge/Dockerfile before WhatsApp rotates the floor."

if [[ -z "${OWN_JID_SEND:-}" ]]; then
    log "WARNING: OWN_JID_SEND not set, skipping self-chat alert"
    log "ALERT (log only): $MSG"
    echo "{\"event\":\"whatsmeow_drift_detected\",\"ahead_by\":$AHEAD_BY,\"pinned\":\"$PINNED\",\"head\":\"$HEAD_SHA\",\"alerted\":false}"
    exit 0
fi

# Bridge may be down; don't fail the whole job if so — still exit 0 with log.
HTTP_CODE=$(curl -sS -o /tmp/twincore-drift-resp.$$ -w "%{http_code}" \
    -X POST "$BRIDGE_URL/api/send" \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "$(printf '{"recipient":"%s","message":"%s"}' "$OWN_JID_SEND" "$MSG")" \
    2>/dev/null || echo "000")
BODY=$(cat /tmp/twincore-drift-resp.$$ 2>/dev/null || true)
rm -f /tmp/twincore-drift-resp.$$

ALERTED=false
if [[ "$HTTP_CODE" =~ ^2 ]]; then
    ALERTED=true
    log "self-chat alert sent (http=$HTTP_CODE)"
else
    log "WARNING: self-chat send failed http=$HTTP_CODE body=${BODY:0:200}"
fi

echo "{\"event\":\"whatsmeow_drift_detected\",\"ahead_by\":$AHEAD_BY,\"pinned\":\"$PINNED\",\"head\":\"$HEAD_SHA\",\"alerted\":$ALERTED}"
