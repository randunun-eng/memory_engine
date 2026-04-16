# Runbook: Monthly backup drill

> Verifies that backups are real and restorable. Skipping this is how you find out about backup corruption during a real disaster.

## Cadence

Monthly. Target: first Tuesday of the month. Calendar it.

## Prerequisites

- At least three backup artifacts from the last 30 days in offsite storage.
- Access to the `age` private key used to encrypt backups.
- A fresh VM (or local Docker container) to restore onto — NOT the production host.
- `memory-engine` installable on the drill target.

## Drill procedure

### 1. Pick a random backup

```bash
# List backups from last 30 days
aws s3 ls s3://<bucket>/memory_engine_backups/ --recursive \
  | awk '$1 > strftime("%Y-%m-%d", systime()-30*86400)'

# Pick one at random
aws s3 cp s3://<bucket>/memory_engine_backups/<chosen>.tar.age /tmp/drill.tar.age
```

Random selection matters. Always drilling the latest backup means never discovering that 2-week-old backups are corrupted.

### 2. Provision the drill target

```bash
# Example: a fresh Ubuntu VM
# Install prerequisites:
apt-get install -y age sqlite3
# Install uv and memory-engine (matching production version):
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/randunun-eng/memory_engine /opt/memory_engine
cd /opt/memory_engine
git checkout <tag-matching-prod>
uv sync
uv pip install --system sqlite-vec
```

Note: match the git tag of production. Backups from v0.3 may not restore cleanly onto v0.5 binaries without running intermediate migrations.

### 3. Decrypt and restore

```bash
# Decrypt
age -d -i ~/keys/backup_age.key -o /tmp/drill.tar /tmp/drill.tar.age

# Extract
mkdir -p /opt/memory_engine/data
tar -xf /tmp/drill.tar -C /opt/memory_engine/data
tar -xf /opt/memory_engine/data/data.tar -C /opt/memory_engine/data

# Move DB to expected path
mv /opt/memory_engine/data/engine.db /opt/memory_engine/data/engine.db

# Verify manifest
cd /opt/memory_engine/data && sha256sum -c manifest.sha256
```

Every file in the manifest must verify. Any mismatch = backup corruption = real problem.

### 4. Start the engine against the restored DB

```bash
cd /opt/memory_engine
export MEMORY_ENGINE_DB_URL="sqlite:///opt/memory_engine/data/engine.db"
uv run memory-engine db status
```

Expected: the migration chain matches what production had applied at the backup time. If different, something is wrong.

### 5. Run `doctor`

```bash
uv run memory-engine doctor
```

Expected: all checks pass. Specifically:
- DB integrity check passes.
- No orphan rows.
- No invariant violations.
- Event hash sampling: pick 10 random event IDs, verify content_hash is still valid against the stored payload.

### 6. Sanity-check retrieval

```bash
# Pick a known persona
uv run memory-engine seed-neurons --dry-run

# Run a recall
curl -X POST http://localhost:4000/v1/recall \
  -H 'content-type: application/json' \
  -d '{"persona_slug":"<known-persona>","query":"test","lens":"self","top_k":5}'
```

Expected: returns results (not an error; may be empty if the persona had no neurons at backup time).

### 7. Record results

Create `docs/runbooks/drills/YYYY-MM.md`:

```markdown
# <YYYY-MM> Backup Drill

## Backup selected
- Artifact: <filename>
- Encrypted at: <timestamp>
- Age: <days old>

## Drill target
- Host: <VM or container details>
- memory-engine version: <tag/commit>

## Results
- Decrypt: <pass/fail>
- Manifest verify: <pass/fail>
- Migrations match production: <yes/no>
- `doctor` passes: <yes/no>
- Retrieval sanity check: <yes/no>
- **Full RTO:** <minutes from start of restore to serving recall traffic>

## Observations
- <anything unexpected>

## Action items
- <any follow-ups>
```

Commit the file to the repo.

## Failure response

If any step fails, it's a production-grade incident. Treat it as:

1. Open an issue tagged `backup-integrity`.
2. Investigate immediately. If the cause is unclear, retry with a different (older or newer) backup to bisect.
3. Do NOT wait for the next monthly drill to re-verify. Re-drill weekly until three consecutive drills pass.

Specific failure modes to check for:

- **Decryption fails** — age key mismatch or file corruption in transit. Verify checksum of the raw `.tar.age` against what the backup script recorded.
- **Manifest checksum mismatch** — content-level corruption. Check the offsite-sync process for silent truncation.
- **`doctor` finds violations** — production had violations at backup time. Check the production healing log for the same timestamp.
- **RTO > 2 hours** — backup artifact is too large. Investigate: unexpected `data/media/` growth? Vacuum the DB (`VACUUM;` reduces size).

## After the drill

- Destroy the drill VM/container. The restored data must not live beyond the drill — it's a snapshot from production.
- Shred any decrypted copies of backup files on local disk:
  ```bash
  shred -u /tmp/drill.tar /opt/memory_engine/data/*.db
  ```

Done. Calendar the next drill for next month's first Tuesday.
