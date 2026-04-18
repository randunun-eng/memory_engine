#!/usr/bin/env bash
#
# twincore-alpha backup script.
#
# Snapshots all persistent state for the 4-service twincore deployment
# into an age-encrypted tarball. Safe to run against live containers —
# SQLite databases use the online backup API (concurrent-write safe).
#
# What gets backed up:
#   - memory-engine-data/engine.db       (events, neurons, synapses)
#   - control-plane-data/control.db      (drafts, approvals, contact_profiles)
#   - whatsapp-data/whatsapp.db          (whatsmeow auth — losing = re-QR)
#   - whatsapp-data/messages.db          (scraped message history)
#   - personas/                          (signed identity YAMLs)
#   - twincore-state/                    (twin-agent checkpoint, etc)
#   - .env                               (secrets — the reason we encrypt)
#
# Usage:
#   bin/backup-twincore.sh
#
# Environment (with sensible macOS defaults):
#   TWINCORE_ROOT     live deployment path       default: script's parent dir
#   BACKUP_DEST       local backup directory     default: ~/TwincoreBackups
#   AGE_RECIPIENT     age public key (age1...)   default: derived from
#                                                ~/.config/twincore/age-key.txt
#   RETENTION_DAYS    prune backups older than   default: 30
#
# Schedule via launchd: see bin/install-backup-schedule.sh

set -euo pipefail

# --- Config ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TWINCORE_ROOT="${TWINCORE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUP_DEST="${BACKUP_DEST:-$HOME/TwincoreBackups}"
AGE_KEY_FILE="${AGE_KEY_FILE:-$HOME/.config/twincore/age-key.txt}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

# Derive AGE_RECIPIENT from the key file if not set explicitly.
if [[ -z "${AGE_RECIPIENT:-}" ]]; then
    if [[ -f "$AGE_KEY_FILE" ]]; then
        AGE_RECIPIENT=$(grep -E "^# public key:" "$AGE_KEY_FILE" | awk '{print $NF}')
    fi
fi

if [[ -z "${AGE_RECIPIENT:-}" ]]; then
    echo "ERROR: AGE_RECIPIENT not set and no key at $AGE_KEY_FILE" >&2
    echo "Run: age-keygen -o $AGE_KEY_FILE && chmod 600 $AGE_KEY_FILE" >&2
    exit 2
fi

# --- Paths ---
TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
STAGE=$(mktemp -d -t twincore_backup.XXXXXX)
ARTIFACT_NAME="twincore_${TS}.tar.gz.age"
ARTIFACT="$BACKUP_DEST/$ARTIFACT_NAME"

# --- Cleanup on exit (shreds plaintext staged files) ---
cleanup() {
    if [[ -d "$STAGE" ]]; then
        find "$STAGE" -type f -exec rm -Pf {} \; 2>/dev/null || true
        rm -rf "$STAGE"
    fi
}
trap cleanup EXIT

mkdir -p "$BACKUP_DEST"
mkdir -p "$STAGE/memory-engine-data" "$STAGE/control-plane-data" "$STAGE/whatsapp-data"

echo "[$(date -u +%H:%M:%SZ)] backup start root=$TWINCORE_ROOT dest=$BACKUP_DEST"

# --- 1. Snapshot each SQLite DB via online backup API ---
# This is the concurrent-safe equivalent of cp — won't corrupt the file
# even if the container is writing to it during the snapshot.
snapshot_db() {
    local rel_path="$1"
    local src="$TWINCORE_ROOT/$rel_path"
    local dst="$STAGE/$rel_path"
    if [[ -f "$src" ]]; then
        mkdir -p "$(dirname "$dst")"
        sqlite3 "$src" ".backup '$dst'"
        echo "[$(date -u +%H:%M:%SZ)]   snapshot: $rel_path"
    else
        echo "[$(date -u +%H:%M:%SZ)]   skip (missing): $rel_path"
    fi
}

snapshot_db "memory-engine-data/engine.db"
snapshot_db "control-plane-data/control.db"
snapshot_db "whatsapp-data/whatsapp.db"
snapshot_db "whatsapp-data/messages.db"

# --- 2. Copy non-DB artifacts (cp is fine — these are not concurrent-write) ---
copy_dir() {
    local rel="$1"
    if [[ -d "$TWINCORE_ROOT/$rel" ]]; then
        cp -R "$TWINCORE_ROOT/$rel" "$STAGE/$rel"
        echo "[$(date -u +%H:%M:%SZ)]   copy: $rel/"
    fi
}

copy_dir "personas"
copy_dir "twincore-state"
copy_dir "whatsapp-auth"

# .env is critical — it holds GEMINI_API_KEY, MCP signing keys, vault key,
# persona-owner keypair. Without it, restored DBs are useless.
if [[ -f "$TWINCORE_ROOT/.env" ]]; then
    cp "$TWINCORE_ROOT/.env" "$STAGE/.env"
    echo "[$(date -u +%H:%M:%SZ)]   copy: .env"
fi

# --- 3. Manifest (SHA-256 of every file) ---
(cd "$STAGE" && find . -type f ! -name manifest.sha256 -print0 \
    | xargs -0 shasum -a 256 > manifest.sha256)
echo "[$(date -u +%H:%M:%SZ)]   manifest: $(wc -l < "$STAGE/manifest.sha256" | tr -d ' ') files"

# --- 4. Compress + encrypt ---
# Pipe tar → gzip → age directly so no plaintext tarball hits disk.
tar -cf - -C "$STAGE" . | gzip -9 | age -r "$AGE_RECIPIENT" -o "$ARTIFACT"

if [[ ! -s "$ARTIFACT" ]]; then
    echo "ERROR: encryption produced empty artifact" >&2
    exit 1
fi

# Post-write steps below are non-fatal. If the backup destination is on a
# macOS TCC-protected mount (Google Drive, iCloud, Dropbox), launchd-spawned
# processes can write new files but may be denied stat/read on them —
# cosmetic, the actual backup is already on disk.
SIZE=$(wc -c < "$ARTIFACT" 2>/dev/null | tr -d ' ' || echo 0)
SIZE_KB=$((SIZE / 1024))

# --- 5. Retention prune (also non-fatal on TCC-protected dests) ---
find "$BACKUP_DEST" -name "twincore_*.tar.gz.age" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

# --- 6. Report ---
echo "[$(date -u +%H:%M:%SZ)] backup complete: $ARTIFACT_NAME (${SIZE_KB} KB)"
echo "{\"event\":\"twincore_backup_completed\",\"artifact\":\"$ARTIFACT_NAME\",\"size_bytes\":$SIZE,\"dest\":\"$BACKUP_DEST\",\"ts\":\"$TS\"}"
