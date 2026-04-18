#!/usr/bin/env bash
#
# twincore-alpha restore script (companion to backup-twincore.sh).
#
# Decrypts an age-encrypted backup and verifies the manifest into a
# staging directory. Does NOT overwrite the live deployment by default —
# the operator copies files across manually after `docker compose down`.
#
# Usage:
#   bin/restore-twincore.sh <artifact.tar.gz.age> [stage_dir]
#
# Environment:
#   AGE_KEY_FILE   age identity file (default: ~/.config/twincore/age-key.txt)
#
# To actually swap in a restore:
#   1. docker compose down
#   2. mv memory-engine-data memory-engine-data.pre-restore
#   3. mv whatsapp-data whatsapp-data.pre-restore
#      (same for control-plane-data, personas, whatsapp-auth, twincore-state)
#   4. cp -R <stage_dir>/* <live_root>/
#   5. docker compose up -d
#   6. Verify before deleting the .pre-restore copies.

set -euo pipefail

ARTIFACT="${1:-}"
STAGE_DIR="${2:-}"
if [[ -z "$ARTIFACT" || ! -f "$ARTIFACT" ]]; then
    echo "Usage: $0 <artifact.tar.gz.age> [stage_dir]" >&2
    echo "  Artifacts typically live at ~/TwincoreBackups/" >&2
    exit 2
fi

AGE_KEY_FILE="${AGE_KEY_FILE:-$HOME/.config/twincore/age-key.txt}"
if [[ ! -f "$AGE_KEY_FILE" ]]; then
    echo "ERROR: age key not found at $AGE_KEY_FILE" >&2
    exit 2
fi

if [[ -z "$STAGE_DIR" ]]; then
    STAGE_DIR=$(mktemp -d -t twincore_restore.XXXXXX)
fi
mkdir -p "$STAGE_DIR"

echo "[$(date -u +%H:%M:%SZ)] decrypting $ARTIFACT → $STAGE_DIR"
age -d -i "$AGE_KEY_FILE" "$ARTIFACT" | gzip -d | tar -xf - -C "$STAGE_DIR"

# --- Verify manifest ---
if [[ -f "$STAGE_DIR/manifest.sha256" ]]; then
    echo "[$(date -u +%H:%M:%SZ)] verifying manifest..."
    (cd "$STAGE_DIR" && shasum -a 256 -c manifest.sha256 --quiet)
    echo "[$(date -u +%H:%M:%SZ)] manifest OK ($(wc -l < "$STAGE_DIR/manifest.sha256" | tr -d ' ') files)"
else
    echo "WARNING: no manifest found — artifact may be corrupt or old format" >&2
fi

# --- SQLite integrity check on each restored DB ---
for db in \
    "$STAGE_DIR/memory-engine-data/engine.db" \
    "$STAGE_DIR/control-plane-data/control.db" \
    "$STAGE_DIR/whatsapp-data/whatsapp.db" \
    "$STAGE_DIR/whatsapp-data/messages.db"; do
    if [[ -f "$db" ]]; then
        result=$(sqlite3 "$db" "PRAGMA integrity_check" 2>&1)
        if [[ "$result" == "ok" ]]; then
            echo "[$(date -u +%H:%M:%SZ)] integrity OK: $(basename "$db")"
        else
            echo "ERROR: integrity check failed for $db: $result" >&2
            exit 1
        fi
    fi
done

echo ""
echo "Restore staged at: $STAGE_DIR"
echo "To swap in, see the header of this script."
