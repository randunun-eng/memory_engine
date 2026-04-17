#!/usr/bin/env bash
#
# memory_engine restore script.
#
# Restores an age-encrypted backup artifact produced by bin/backup.sh.
# Verifies integrity (SQLite PRAGMA + manifest checksums) before replacing
# the production database.
#
# Usage:
#   bin/restore.sh <artifact_path_or_url> [--force]
#
# Examples:
#   bin/restore.sh /backup/sales_twin_2026-04-16T12-00-00Z.tar.age
#   bin/restore.sh s3://wiki-v3-backups/host/sales_twin_*.tar.age --force
#
# Environment:
#   MEMORY_ENGINE_BACKUP_IDENTITY   age identity file (age-keygen private key)
#   MEMORY_ENGINE_DATA_DIR          Engine data directory (default: data)
#
# Exit codes:
#   0  restore complete
#   1  failure (integrity check, decryption, disk, etc.)
#   2  usage error
#   3  user declined (without --force)
#
# Safety:
#   - Never overwrites existing data without backup copy to data/engine.db.pre-restore
#   - Runs PRAGMA integrity_check before activating restored DB
#   - Stops the engine service if detected (systemctl)
#   - --force skips the interactive confirmation

set -euo pipefail

ARTIFACT_INPUT="${1:-}"
FORCE="${2:-}"

if [[ -z "$ARTIFACT_INPUT" ]]; then
    echo "Usage: $0 <artifact_path_or_url> [--force]" >&2
    exit 2
fi

IDENTITY="${MEMORY_ENGINE_BACKUP_IDENTITY:?MEMORY_ENGINE_BACKUP_IDENTITY not set}"
DATA_DIR="${MEMORY_ENGINE_DATA_DIR:-data}"

STAGE=$(mktemp -d -t memory_engine_restore.XXXXXX)
cleanup() {
    if [[ -d "$STAGE" ]]; then
        find "$STAGE" -type f -exec shred -u {} \; 2>/dev/null || true
        rm -rf "$STAGE"
    fi
}
trap cleanup EXIT

log() {
    echo "[$(date -u +%H:%M:%SZ)] $*"
}

# --- 1. Fetch artifact if remote ---
log "Fetching artifact..."
case "$ARTIFACT_INPUT" in
    s3://*)
        aws s3 cp "$ARTIFACT_INPUT" "$STAGE/artifact.tar.age" --no-progress
        ;;
    gs://*)
        gcloud storage cp "$ARTIFACT_INPUT" "$STAGE/artifact.tar.age"
        ;;
    /*|./*)
        cp "$ARTIFACT_INPUT" "$STAGE/artifact.tar.age"
        ;;
    *)
        echo "ERROR: unsupported artifact scheme: $ARTIFACT_INPUT" >&2
        exit 1
        ;;
esac

# --- 2. Decrypt ---
log "Decrypting..."
age -d -i "$IDENTITY" -o "$STAGE/bundle.tar" "$STAGE/artifact.tar.age"

# --- 3. Unpack ---
log "Unpacking..."
tar -xf "$STAGE/bundle.tar" -C "$STAGE"

# Must contain engine.db, data.tar, manifest.sha256
for f in engine.db data.tar manifest.sha256; do
    if [[ ! -f "$STAGE/$f" ]]; then
        echo "ERROR: artifact missing expected file: $f" >&2
        exit 1
    fi
done

# --- 4. Verify manifest checksums ---
log "Verifying manifest..."
(cd "$STAGE" && sha256sum -c manifest.sha256) || {
    echo "ERROR: manifest verification failed" >&2
    exit 1
}

# --- 5. SQLite integrity check ---
log "Running SQLite integrity check..."
INTEGRITY=$(sqlite3 "$STAGE/engine.db" "PRAGMA integrity_check;")
if [[ "$INTEGRITY" != "ok" ]]; then
    echo "ERROR: integrity check failed: $INTEGRITY" >&2
    exit 1
fi
log "Integrity check: ok"

# --- 6. Confirm with operator (unless --force) ---
TARGET_DB="$DATA_DIR/engine.db"
if [[ "$FORCE" != "--force" ]]; then
    if [[ -f "$TARGET_DB" ]]; then
        echo
        echo "This will replace $TARGET_DB"
        echo "A backup copy will be written to $TARGET_DB.pre-restore"
    fi
    read -rp "Continue? [y/N] " ans
    if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
        log "Restore declined."
        exit 3
    fi
fi

# --- 7. Stop engine if running (best-effort) ---
if command -v systemctl &>/dev/null; then
    if systemctl is-active --quiet memory-engine 2>/dev/null; then
        log "Stopping memory-engine service..."
        sudo systemctl stop memory-engine || log "WARNING: could not stop service; continuing"
    fi
fi

# --- 8. Backup existing DB ---
mkdir -p "$DATA_DIR"
if [[ -f "$TARGET_DB" ]]; then
    log "Backing up current DB to ${TARGET_DB}.pre-restore"
    mv "$TARGET_DB" "${TARGET_DB}.pre-restore"
fi

# --- 9. Install restored DB ---
log "Installing restored DB..."
mv "$STAGE/engine.db" "$TARGET_DB"

# --- 10. Restore data bundle (media, identity) ---
if [[ -s "$STAGE/data.tar" ]]; then
    log "Restoring data bundle..."
    tar -xf "$STAGE/data.tar" -C "$DATA_DIR"
fi

# --- 11. Start engine (best-effort) ---
if command -v systemctl &>/dev/null; then
    if systemctl list-unit-files memory-engine.service &>/dev/null; then
        log "Starting memory-engine service..."
        sudo systemctl start memory-engine || log "WARNING: could not start; start manually"
    fi
fi

log "Restore complete."
echo "{\"event\":\"restore_completed\",\"artifact\":\"$ARTIFACT_INPUT\",\"target\":\"$TARGET_DB\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
