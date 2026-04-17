# Runbook: GroundingGateRejectRateHigh

**Severity:** warning

**What this means (one sentence):**
The grounding gate is rejecting > 40% of candidate neurons — the extractor is producing ungrounded content at high rates, likely a prompt regression.

**Immediate action (do first):**
1. When did the reject rate change? Dashboard A → grounding pass rate panel. Correlate with recent prompt promotions.
2. List recent prompt changes:
   ```sql
   SELECT site, version, active, created_at FROM prompt_templates
   ORDER BY created_at DESC LIMIT 10;
   ```
3. Check shadow comparison data for alternative prompts:
   ```sql
   SELECT * FROM prompt_comparison_daily
   WHERE site = 'extract_entities' AND day >= date('now', '-7 days')
   ORDER BY day DESC;
   ```

**Diagnostic steps:**
- Sample rejected candidates: `SELECT candidate_json, reason FROM quarantine_neurons WHERE reason LIKE 'low_similarity' OR reason LIKE 'llm_judge_ungrounded' ORDER BY created_at DESC LIMIT 20;`
- Read the extracted claims. Are they in the source events? If not, the extractor is hallucinating. If yes, the grounding similarity is tuned wrong.

**Common causes, most to least likely:**
1. **Recently promoted prompt regressed.** Fix: `memory-engine prompt rollback extract_entities`. Must complete in <60s.
2. **Embedder model changed without `embedder_rev` bump.** Fix: verify `embedder_rev` in config matches deployed model. See `sqlite_vec_install.md`.
3. **Source events unusually noisy** (e.g. voice transcripts, forwarded messages from low-quality sources). Fix: accept temporarily; tune per-source thresholds as future work.
4. **Similarity threshold misconfigured.** Fix: default is 0.40; don't set below 0.30 without a measured grounding-accuracy baseline.

**Remediation:**
If you just promoted a new prompt:
```bash
uv run memory-engine prompt rollback extract_entities
# Log the rollback with reason
uv run memory-engine prompt note --reason "Grounding reject rate > 50% within 2h of promotion"
```

**Escalation:**
If no recent prompt change and reject rate > 60%, escalate. Systemic extractor issue.

**Related:**
- runbooks/quarantine_review.md
- docs/blueprint/05_v0.4.md §4.4 (grounding gate)
