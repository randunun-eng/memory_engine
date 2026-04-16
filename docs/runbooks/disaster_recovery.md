# Runbook: Disaster recovery

> Total-loss recovery path. When the production host is gone — deleted, ransomed, hardware-dead, region-evacuated. Target RTO: 2 hours. Target RPO: 5 minutes (with WAL archival) or 6 hours (backups only).

## Prerequisites verified in advance

Before disaster strikes, confirm quarterly:

- [ ] At least 3 backup artifacts from the last 30 days are in offsite storage.
- [ ] The age decryption key is in a location NOT on the production host.
- [ ] You have access credentials for offsite storage (AWS IAM, Backblaze keys, etc.) in a password manager.
- [ ] `docs/runbooks/whatsapp_setup.md` is current.
- [ ] Last monthly backup drill completed successfully.

If any of these are not true, fix before you need them.

## When to invoke

- Production host unreachable for > 30 minutes with no ETA.
- Data corruption detected beyond repair.
- Ransomware or unauthorized access suspected.
- Unrecoverable cloud provider issue.

## Phase 1 — triage (target: within 15 minutes)

### 1.1 Confirm the disaster

```bash
# Can you ssh in?
ssh <engine-host>

# Can you reach the metrics endpoint?
curl -sf http://<engine-host>:4000/metrics

# Is the cloud provider reporting an incident?
# AWS: https://health.aws.amazon.com/health/status
# Oracle Cloud: https://ocistatus.oraclecloud.com
# Hetzner: https://status.hetzner.com
```

If any of these work, it might not be a disaster — try the `halt_investigation.md` runbook first.

### 1.2 Notify

Even if this is a solo deployment: write a note somewhere (journal, issue tracker) with the start time. Post-incident review needs a timeline.

### 1.3 Stop inbound traffic

If Meta is still sending webhooks to a dead endpoint, messages are being lost. Temporarily disable the WhatsApp webhook in Meta's developer console. This is reversible once the new host is live.

## Phase 2 — recovery (target: within 90 minutes)

### 2.1 Provision a fresh host

Match the original host's specs: 4 OCPU, 24 GB RAM, 100+ GB disk, Ubuntu 24 LTS or equivalent. Assign a stable IP or prepare to update DNS.

```bash
# Minimum prerequisites on the fresh host
sudo apt-get update
sudo apt-get install -y git curl age sqlite3 systemd

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

### 2.2 Clone the repo

```bash
sudo mkdir -p /opt/memory_engine
sudo chown $USER:$USER /opt/memory_engine
git clone https://github.com/randunun-eng/memory_engine /opt/memory_engine
cd /opt/memory_engine

# Match the tag of the last known-good production version.
# You recorded this in the deployment log, right?
git checkout <last-known-good-tag>

uv sync --all-extras
uv pip install --system sqlite-vec
```

### 2.3 Restore the most recent backup

```bash
# Retrieve from offsite
aws s3 ls s3://<bucket>/memory_engine_backups/ --recursive | tail -5
aws s3 cp s3://<bucket>/memory_engine_backups/<latest>.tar.age /tmp/restore.tar.age

# Decrypt (age key is NOT on this host yet; fetch from secret manager or password manager)
# Write the private key to a temp file, shredded after:
cat > /tmp/age.key << 'EOF'
# paste private key here
EOF
chmod 600 /tmp/age.key

age -d -i /tmp/age.key -o /tmp/restore.tar /tmp/restore.tar.age
shred -u /tmp/age.key

# Extract
mkdir -p /opt/memory_engine/data
tar -xf /tmp/restore.tar -C /opt/memory_engine/data
tar -xf /opt/memory_engine/data/data.tar -C /opt/memory_engine/data

# Verify manifest before trusting
cd /opt/memory_engine/data && sha256sum -c manifest.sha256 || { echo "MANIFEST FAILURE — try previous backup"; exit 1; }

# Clean up decrypted tarball
shred -u /tmp/restore.tar
```

If the manifest fails, **do not use this backup**. Try the next-most-recent. Losing a backup is recoverable; restoring corrupted data is not.

### 2.4 Apply any WAL archives newer than the backup

Only if you have WAL archival configured (Phase 6 optional):

```bash
# Fetch WAL segments from offsite newer than the backup's timestamp
aws s3 sync s3://<bucket>/memory_engine_wal/ /tmp/wal_replay/ \
    --exclude "*" --include "*2026-04-16T*"  # adjust date

# Replay (SQLite WAL replay is automatic on next connection if WAL file is in place)
cp /tmp/wal_replay/* /opt/memory_engine/data/
shred -u /tmp/wal_replay/*
rmdir /tmp/wal_replay
```

If no WAL archival, the last backup's recorded_at is your effective RPO (worst case: 6 hours of lost events).

### 2.5 Install secrets

```bash
# Vault key
echo "MEMORY_ENGINE_VAULT_KEY=<from password manager>" >> /opt/memory_engine/.env.local

# LLM provider credentials if using paid endpoints
# echo "ANTHROPIC_API_KEY=..." >> /opt/memory_engine/.env.local

chmod 600 /opt/memory_engine/.env.local
```

### 2.6 Start the engine

```bash
cd /opt/memory_engine

# Verify migration chain matches what the backup expects
uv run memory-engine db status

# Run doctor
uv run memory-engine db doctor  # (Phase 3+)

# If halt state was active before the disaster, force-release since we're
# starting from a known-good restore
uv run memory-engine halt release --reason "DR restore; original halt state may or may not apply" --force || true

# Start the server
uv run memory-engine serve &
```

Set up as a systemd service for resilience (see `docs/runbooks/systemd_service.md`).

### 2.7 Sanity checks

```bash
# Metrics endpoint
curl -sf http://localhost:4000/metrics | head

# Event count matches expectation
curl -s http://localhost:4000/v1/stats | jq .events_total

# Can read a neuron
curl -sX POST http://localhost:4000/v1/recall \
  -H 'content-type: application/json' \
  -d '{"persona_slug":"<known-slug>","query":"test","lens":"self","top_k":1}'
```

## Phase 3 — restore service (target: within 2 hours)

### 3.1 Re-point the webhook

In Meta's developer console, update the WhatsApp webhook URL to point at the new host. Meta will re-verify; the engine responds automatically.

Re-enable the subscription.

### 3.2 Send a test message

From a test number, send a message. Verify in the engine:

```bash
curl -s http://localhost:4000/v1/stats | jq '.events_last_hour'
```

Should increment. If not, check logs:

```bash
journalctl -u memory-engine --since "5 minutes ago"
```

### 3.3 Update DNS if applicable

If the domain name moves to the new IP, update the DNS record and wait for propagation. Low-TTL records (60-300s) help here; keep production DNS at low TTL so DR is faster.

## Phase 4 — post-incident (within 24 hours)

1. Write an incident report at `docs/runbooks/incidents/YYYY-MM-DD-disaster-recovery.md`.
2. Note the actual RTO achieved vs the 2-hour target.
3. Note the actual RPO (difference between the last backup's timestamp and current).
4. Update this runbook with anything you learned.
5. Schedule a drill within the next 30 days to exercise any new steps introduced.

## What to do differently

After any real DR event:
- Review alerting — did we get notified promptly?
- Review documentation — was anything stale?
- Review backup cadence — 6-hour full backups might need to be 3-hour.
- Review WAL archival — was the gap too big?

Recovery is not a one-time concern. Every DR event informs the next iteration of the procedure.

## Commands you'll wish you had bookmarked

```bash
# Backup list
aws s3 ls s3://<bucket>/memory_engine_backups/ --recursive | tail -10

# Force a fresh backup before any risky operation
sudo -u memory-engine /opt/memory_engine/bin/backup.sh <persona_slug>

# Quick engine status
curl -sf http://localhost:4000/metrics | grep memory_engine_halt_state

# Recent events
uv run memory-engine events recent --limit 20
```
