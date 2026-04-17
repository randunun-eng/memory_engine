# Runbook: IdentityFlagSpike

**Severity:** warning

**What this means (one sentence):**
Unusual rate of identity drift flags (> 5/hr) — either the persona is being probed adversarially, or the drift detector is over-firing.

**Immediate action (do first):**
1. Which persona? Which flag_type?
   ```sql
   SELECT persona_id, flag_type, COUNT(*)
   FROM identity_drift_flags
   WHERE flagged_at > datetime('now', '-1 hour')
     AND reviewed_at IS NULL
   GROUP BY persona_id, flag_type
   ORDER BY 3 DESC;
   ```
2. Sample the flags:
   ```sql
   SELECT flag_type, candidate_text, rule_text
   FROM identity_drift_flags
   WHERE flagged_at > datetime('now', '-1 hour')
   LIMIT 10;
   ```
   **Note:** `candidate_text` is PII-redacted per rule 13.

**Diagnostic steps:**
- Is the source a single counterparty? (Possible prompt injection campaign.)
- Is the flag_type `forbidden_topic`? If so, the content matched a forbidden_topic keyword. Check whether the detection was accurate or over-broad.
- Did we recently change the identity document? New non-negotiables produce a burst of flags as legitimate traffic hits them.

**Common causes, most to least likely:**
1. **Legitimate traffic hit a new non-negotiable.** Fix: no action; the system is working. Monitor and adjust rule wording if false-positive rate stays high.
2. **Adversarial counterparty probing** (T11 territory). Fix: the system is designed to resist this. Confirm: drift flags DO NOT modify the identity doc (rule 11), and cross-counterparty isolation holds (T3). Review the specific counterparty.
3. **Drift detector over-firing** due to a prompt / classifier regression. Fix: roll back the drift-detection prompt if recently promoted.
4. **Identity document YAML malformed after edit.** Fix: `uv run memory-engine identity validate --persona <slug>`.

**Remediation:**
Review flags via CLI:
```bash
uv run memory-engine identity flags --persona <slug> --unreviewed
```

Bulk-mark as reviewed with action:
```bash
uv run memory-engine identity review --persona <slug> --flag-type forbidden_topic --action reject
```

**Escalation:**
If the source is a single counterparty with > 20 flags/hour, they are probing. Apply a counterparty-level tombstone if policy allows:
```sql
INSERT INTO tombstones (persona_id, scope, reason)
VALUES (?, 'counterparty:whatsapp:+94...', 'identity probe, reviewed by operator');
```

**Related:**
- docs/blueprint/04_v0.3.md §21 (identity protocol)
- T11 release gate tests (tests/invariants/test_phase5.py)
