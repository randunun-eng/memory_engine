#!/usr/bin/env bash
#
# memory_engine backup script.
#
# Produces an age-encrypted tarball and syncs offsite. Safe to run against
# a live engine — uses SQLite's online backup API for a consistent snapshot
# without blocking writes.
#
# Usage:
#   bin/backup.sh <persona_slug>
#
# Environment:
#   MEMORY_ENGINE_BACKUP_DEST       Destination directory (local) or prefix (s3://...)
#   MEMORY_ENGINE_BACKUP_RECIPIENT  age public key (age1...)
#   MEMORY_ENGINE_DATA_DIR          Engine data directory (default: data)
#
# Cron example (every 6 hours):
#   0 */6 * * * /opt/memory_engine/bin/backup.sh sales_twin >> /var/log/memory_engine_backup.log 2>&1
#
# See docs/runbooks/backup_drill.md for restore verification and
# docs/runbooks/disaster_recovery.md for the full DR path.

set -euo pipefail

# --- Args ---
PERSONA_SLUG="${1:-}"
if [[ -z "$PERSONA_SLUG" ]]; then
    echo "Usage: $0 <persona_slug>" >&2
    exit 2
fi

# --- Env ---
DEST="${MEMORY_ENGINE_BACKUP_DEST:?MEMORY_ENGINE_BACKUP_DEST not set}"
RECIPIENT="${MEMORY_ENGINE_BACKUP_RECIPIENT:?MEMORY_ENGINE_BACKUP_RECIPIENT not set}"
DATA_DIR="${MEMORY_ENGINE_DATA_DIR:-data}"

# --- Paths ---
TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
STAGE=$(mktemp -d -t memory_engine_backup.XXXXXX)
ARTIFACT_NAME="${PERSONA_SLUG}_${TS}.tar.age"

# --- Cleanup on exit ---
cleanup() {
    # Shred any plaintext files before rm -r. Backups must never leave
    # unencrypted copies on disk.
    if [[ -d "$STAGE" ]]; then
        find "$STAGE" -type f -exec shred -u {} \; 2>/dev/null || true
        rm -rf "$STAGE"
    fi
}
trap cleanup EXIT

echo "[$(date -u +%H:%M:%SZ)] Starting backup for persona=$PERSONA_SLUG"

# --- 1. Consistent snapshot of the SQLite DB ---
# Uses the online backup API (safe against concurrent writes; does not block).
DB_SRC="$DATA_DIR/engine.db"
if [[ ! -f "$DB_SRC" ]]; then
    echo "ERROR: $DB_SRC not found" >&2
    exit 1
fi

echo "[$(date -u +%H:%M:%SZ)] Snapshotting DB..."
sqlite3 "$DB_SRC" ".backup '$STAGE/engine.db'"

# --- 2. Bundle data/ directory (media, identity docs, etc.) ---
echo "[$(date -u +%H:%M:%SZ)] Bundling data directory..."
if [[ -d "$DATA_DIR/media" ]] || [[ -d "$DATA_DIR/identity" ]]; then
    tar -cf "$STAGE/data.tar" -C "$DATA_DIR" $(ls -d media identity 2>/dev/null || true) 2>/dev/null || true
else
    # Create an empty tar so the manifest has a consistent layout
    tar -cf "$STAGE/data.tar" --files-from /dev/null
fi

# --- 3. Manifest with checksums ---
echo "[$(date -u +%H:%M:%SZ)] Computing manifest..."
(cd "$STAGE" && sha256sum engine.db data.tar > manifest.sha256)

# --- 4. Bundle ---
echo "[$(date -u +%H:%M:%SZ)] Bundling artifacts..."
tar -cf "$STAGE/bundle.tar" -C "$STAGE" engine.db data.tar manifest.sha256

# --- 5. Encrypt with age ---
echo "[$(date -u +%H:%M:%SZ)] Encrypting..."
age -r "$RECIPIENT" -o "$STAGE/$ARTIFACT_NAME" "$STAGE/bundle.tar"

# Verify encryption produced a file larger than 0 bytes (sanity check)
if [[ ! -s "$STAGE/$ARTIFACT_NAME" ]]; then
    echo "ERROR: encryption produced empty file" >&2
    exit 1
fi

# --- 6. Deliver offsite ---
echo "[$(date -u +%H:%M:%SZ)] Delivering to $DEST..."
case "$DEST" in
    s3://*)
        aws s3 cp "$STAGE/$ARTIFACT_NAME" "$DEST/$ARTIFACT_NAME" --no-progress
        ;;
    gs://*)
        gcloud storage cp "$STAGE/$ARTIFACT_NAME" "$DEST/$ARTIFACT_NAME"
        ;;
    b2://*)
        # Backblaze B2 via rclone or b2 CLI
        b2 upload-file "${DEST#b2://}" "$STAGE/$ARTIFACT_NAME" "$ARTIFACT_NAME"
        ;;
    rclone:*)
        rclone copy "$STAGE/$ARTIFACT_NAME" "${DEST#rclone:}"
        ;;
    /*)
        # Local destination
        mkdir -p "$DEST"
        cp "$STAGE/$ARTIFACT_NAME" "$DEST/$ARTIFACT_NAME"
        ;;
    *)
        echo "ERROR: unknown destination scheme: $DEST" >&2
        exit 1
        ;;
esac

# --- 7. Report success ---
ARTIFACT_SIZE=$(wc -c < "$STAGE/$ARTIFACT_NAME")
ARTIFACT_SIZE_MB=$((ARTIFACT_SIZE / 1024 / 1024))

echo "[$(date -u +%H:%M:%SZ)] Backup complete: $ARTIFACT_NAME (${ARTIFACT_SIZE_MB} MB)"

# Emit a structured log line for dashboards
echo "{\"event\":\"backup_completed\",\"persona_slug\":\"$PERSONA_SLUG\",\"artifact\":\"$ARTIFACT_NAME\",\"size_bytes\":$ARTIFACT_SIZE,\"dest\":\"$DEST\",\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"

# cleanup trap runs on exit
