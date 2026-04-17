#!/usr/bin/env bash
#
# memory_engine disaster recovery drill.
#
# Restores the most recent backup to a temporary directory, runs integrity
# checks, and measures RTO. Never touches production data. Writes a drill
# report to drills/YYYY-MM-DD.md.
#
# Run monthly (cron recommended). First drill must complete before the
# first production user (Phase 7 gate).
#
# Usage:
#   bin/drill.sh <persona_slug>
#
# Environment:
#   MEMORY_ENGINE_BACKUP_DEST       Backup destination (to list/pull from)
#   MEMORY_ENGINE_BACKUP_IDENTITY   age identity file
#   MEMORY_ENGINE_RTO_SECONDS       Target RTO in seconds (default: 7200 = 2h)
#
# Exit codes:
#   0  drill passed
#   1  drill failed (integrity, RTO exceeded, missing backup)
#   2  usage error

set -euo pipefail

PERSONA_SLUG="${1:-}"
if [[ -z "$PERSONA_SLUG" ]]; then
    echo "Usage: $0 <persona_slug>" >&2
    exit 2
fi

DEST="${MEMORY_ENGINE_BACKUP_DEST:?MEMORY_ENGINE_BACKUP_DEST not set}"
IDENTITY="${MEMORY_ENGINE_BACKUP_IDENTITY:?MEMORY_ENGINE_BACKUP_IDENTITY not set}"
RTO_SECONDS="${MEMORY_ENGINE_RTO_SECONDS:-7200}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRILLS_DIR="$REPO_ROOT/drills"
mkdir -p "$DRILLS_DIR"

DRILL_DATE=$(date -u +%Y-%m-%d)
DRILL_REPORT="$DRILLS_DIR/${DRILL_DATE}.md"
STAGE=$(mktemp -d -t memory_engine_drill.XXXXXX)

cleanup() {
    if [[ -d "$STAGE" ]]; then
        find "$STAGE" -type f -exec shred -u {} \; 2>/dev/null || true
        rm -rf "$STAGE"
    fi
}
trap cleanup EXIT

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

# ---- Find most recent backup ----
log "Finding latest backup for persona=$PERSONA_SLUG"

START_TS=$(date -u +%s)

case "$DEST" in
    s3://*)
        LATEST=$(aws s3 ls "$DEST/" | grep "${PERSONA_SLUG}_" | sort | tail -1 | awk '{print $4}')
        if [[ -z "$LATEST" ]]; then
            echo "ERROR: no backup found for $PERSONA_SLUG at $DEST" >&2
            exit 1
        fi
        ARTIFACT_URI="$DEST/$LATEST"
        ;;
    /*)
        LATEST=$(ls -t "$DEST"/${PERSONA_SLUG}_*.tar.age 2>/dev/null | head -1 || true)
        if [[ -z "$LATEST" ]]; then
            echo "ERROR: no backup found for $PERSONA_SLUG at $DEST" >&2
            exit 1
        fi
        ARTIFACT_URI="$LATEST"
        ;;
    *)
        echo "ERROR: unsupported destination scheme: $DEST" >&2
        exit 1
        ;;
esac

log "Latest artifact: $ARTIFACT_URI"

# ---- Pull, decrypt, unpack ----
log "Pulling artifact..."
case "$ARTIFACT_URI" in
    s3://*) aws s3 cp "$ARTIFACT_URI" "$STAGE/artifact.tar.age" --no-progress ;;
    *)      cp "$ARTIFACT_URI" "$STAGE/artifact.tar.age" ;;
esac

log "Decrypting..."
age -d -i "$IDENTITY" -o "$STAGE/bundle.tar" "$STAGE/artifact.tar.age"

log "Unpacking..."
tar -xf "$STAGE/bundle.tar" -C "$STAGE"

# ---- Integrity checks ----
log "Verifying manifest..."
MANIFEST_OK=true
(cd "$STAGE" && sha256sum -c manifest.sha256) || MANIFEST_OK=false

log "Running SQLite integrity_check..."
INTEGRITY=$(sqlite3 "$STAGE/engine.db" "PRAGMA integrity_check;")
INTEGRITY_OK=true
[[ "$INTEGRITY" == "ok" ]] || INTEGRITY_OK=false

# ---- Row counts (sanity) ----
EVENTS=$(sqlite3 "$STAGE/engine.db" "SELECT COUNT(*) FROM events;" 2>/dev/null || echo 0)
NEURONS=$(sqlite3 "$STAGE/engine.db" "SELECT COUNT(*) FROM neurons;" 2>/dev/null || echo 0)
PERSONAS=$(sqlite3 "$STAGE/engine.db" "SELECT COUNT(*) FROM personas;" 2>/dev/null || echo 0)

# ---- Measure RTO ----
END_TS=$(date -u +%s)
ELAPSED=$((END_TS - START_TS))
RTO_OK=true
[[ "$ELAPSED" -le "$RTO_SECONDS" ]] || RTO_OK=false

# ---- Verdict ----
DRILL_VERDICT="PASS"
if ! $MANIFEST_OK || ! $INTEGRITY_OK || ! $RTO_OK; then
    DRILL_VERDICT="FAIL"
fi

# ---- Write report ----
cat > "$DRILL_REPORT" <<EOF
# DR Drill — ${DRILL_DATE}

**Verdict:** $DRILL_VERDICT
**Persona:** $PERSONA_SLUG
**Artifact:** $ARTIFACT_URI

## Timing

| Metric | Value | Target | OK? |
|--------|-------|--------|-----|
| Elapsed | ${ELAPSED}s | ${RTO_SECONDS}s | $($RTO_OK && echo yes || echo no) |

## Integrity

| Check | Result |
|-------|--------|
| Manifest checksums | $($MANIFEST_OK && echo ok || echo FAIL) |
| SQLite integrity_check | $INTEGRITY |

## Row counts

| Table | Rows |
|-------|------|
| personas | $PERSONAS |
| events | $EVENTS |
| neurons | $NEURONS |

## Next steps

$(if [[ "$DRILL_VERDICT" == "PASS" ]]; then
    echo "- Drill passed. Schedule next drill in 30 days."
else
    echo "- **Drill failed. Remediate before next sprint.** See docs/runbooks/disaster_recovery.md."
fi)
EOF

log "Drill report: $DRILL_REPORT"
log "Verdict: $DRILL_VERDICT (elapsed ${ELAPSED}s, RTO target ${RTO_SECONDS}s)"

# Emit structured log line
echo "{\"event\":\"dr_drill_completed\",\"persona_slug\":\"$PERSONA_SLUG\",\"verdict\":\"$DRILL_VERDICT\",\"elapsed_seconds\":$ELAPSED,\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"

if [[ "$DRILL_VERDICT" != "PASS" ]]; then
    exit 1
fi
