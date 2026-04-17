# Runbook: QuarantineDepthGrowing

**Severity:** warning

**What this means (one sentence):**
Rejected neuron candidates are piling up unreviewed. Quarantine is designed to surface bad extraction / grounding, not silently hoard rejects.

**Immediate action (do first):**
1. Count per persona and reason:
   ```sql
   SELECT persona_id, reason, COUNT(*)
   FROM quarantine_neurons
   WHERE reviewed_at IS NULL
   GROUP BY persona_id, reason
   ORDER BY 3 DESC;
   ```
2. Look at the dominant `reason`. Patterns:
   - Mostly `citation_unresolved` → extractor producing invalid event refs
   - Mostly `low_similarity` → extractor hallucinating content not in sources
   - Mostly `llm_judge_ungrounded` → semantic/procedural candidates losing the LLM judge

**Diagnostic steps:**
- Sample 10 entries: `SELECT candidate_json, reason FROM quarantine_neurons WHERE reviewed_at IS NULL LIMIT 10;`
- Read the JSON. Are they legitimately bad, or is the gate too strict?
- Check the active extraction prompt version. Recent rollback? Recent promotion that regressed?

**Common causes, most to least likely:**
1. **Extraction prompt regressed after promotion.** Fix: rollback via `memory-engine prompt rollback extract_entities`. Rescreen affected neurons.
2. **Grounding threshold too strict for domain.** Fix: review `config/default.toml` `grounding.similarity_threshold`. Don't lower below 0.30.
3. **Genuine noisy inputs** (voice transcripts, OCR'd images). Fix: expected. Review manually; promote salvageable candidates.
4. **Healer not processing the queue.** Fix: check healer loop running. Review cadence from CLAUDE.md §8.

**Remediation:**
Manual review:
```bash
uv run memory-engine quarantine review --persona <slug> --limit 20
```

Or bulk rescreen after prompt fix:
```bash
uv run memory-engine heal rescreen --produced-under v3.3.0-rc1
```

**Escalation:**
If depth grows > 1000 without operator attention, this alert becomes critical — it indicates the review process itself has failed.

**Related:**
- runbooks/extractor_quality.md
