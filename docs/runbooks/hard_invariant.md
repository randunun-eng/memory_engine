# Runbook: HardInvariantViolation

**Severity:** critical

**What this means (one sentence):**
A privacy, leak, or scope invariant was violated — the system engaged halt and is refusing writes.

**Immediate action (do first):**
1. Open Dashboard A (Operations Overview). The red "Hard invariant violations" tile tells you the count.
2. Query `healing_log` for the violation details:
   ```sql
   SELECT invariant_name, details, detected_at FROM healing_log
   WHERE severity = 'critical' AND resolved_at IS NULL
   ORDER BY detected_at DESC LIMIT 10;
   ```
3. Check halt state: `uv run memory-engine halt status`

**Diagnostic steps:**
- Read the violation `details` JSON. It contains the offending row id, the check that failed, and (if possible) sample data.
- Determine which invariant fired:
  - `no_cross_counterparty_leak` → a neuron cites events for a different counterparty
  - `counterparty_fact_requires_counterparty_id` → schema-level partition broken
  - `no_dangling_citations` → a neuron references a non-existent event id
  - `distinct_exceeds_source` → `distinct_source_count > source_count` (should be impossible)
- Check recent migrations and deployments for changes near the affected tables.

**Common causes, most to least likely:**
1. **Code path that writes neurons bypassed `append_neuron()`.** Fix: route all writes through the single writer. Verify no direct SQL INSERTs in new code.
2. **Test fixture leaked into production DB.** Fix: identify the source, quarantine the affected rows, release halt.
3. **Schema drift (trigger dropped, CHECK removed).** Fix: re-run migrations, verify triggers via `sqlite_master`.
4. **Genuine bug in extractor producing cross-counterparty citations.** Fix: rollback extractor prompt; rescreen affected neurons.

**Release halt:**
Only after root cause is understood and fix deployed:
```bash
uv run memory-engine halt release --reason "Fixed extractor prompt v3.2.1 → v3.1.0 rollback"
```

**Escalation:**
If the halt has been engaged for > 2 hours without root cause, escalate to project owner. A halt is not a minor incident.

**Related:**
- runbooks/halt_investigation.md — deeper forensics
- runbooks/halt_release_emergency.md — emergency override (requires written justification)
