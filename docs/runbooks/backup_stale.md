# Runbook: BackupStale

**Severity:** critical

**What this means (one sentence):**
Last successful backup was > 1 hour ago — RPO at risk; a disaster right now means more than 1 hour of data loss.

**Immediate action (do first):**
1. Check the backup status:
   ```sql
   SELECT persona_id, last_success_at, last_destination
   FROM backup_status
   ORDER BY last_success_at ASC;
   ```
2. Try a manual backup:
   ```bash
   MEMORY_ENGINE_BACKUP_DEST=... MEMORY_ENGINE_BACKUP_RECIPIENT=... \
     bin/backup.sh <persona_slug>
   ```
3. Is cron / systemd-timer running? `systemctl list-timers | grep memory-engine`

**Diagnostic steps:**
- If manual backup succeeds, the scheduler is broken. Check cron/systemd logs.
- If manual backup fails, read the error:
  - `age` not found → install age
  - `s3 cp` fails → credentials expired or bucket policy changed
  - `sqlite3 .backup` fails → DB locked or disk full
- Check the age recipient public key is correct.

**Common causes, most to least likely:**
1. **Scheduler stopped** (cron dead, systemd timer disabled). Fix: re-enable. Check why it stopped — often a permission error written once and ignored.
2. **Backup destination credential expired** (AWS access key rotated, B2 app key revoked). Fix: re-create credentials; update environment.
3. **Disk full on engine host** (staging area). Fix: free space; backup staging needs ~1.5x DB size free.
4. **Network outage to offsite storage.** Fix: temporary — backup should succeed on retry. If network is ongoing issue, add local-then-sync pattern.
5. **age identity / recipient mismatch after rotation.** Fix: verify recipient matches the identity held by the operator for restore.

**Immediate containment:**
Force a backup right now to reduce RPO exposure:
```bash
bin/backup.sh <persona_slug>
```

**Verification:**
After the next scheduled backup, verify `backup_status.last_success_at` is fresh and dashboard tile is green.

**Escalation:**
If backups have been broken for > 24h, run a DR drill (`bin/drill.sh`) to validate the last known-good artifact is restorable. A silent backup failure combined with an untested artifact is worst-case.

**Related:**
- bin/backup.sh
- bin/restore.sh
- runbooks/backup_drill.md
- runbooks/disaster_recovery.md
