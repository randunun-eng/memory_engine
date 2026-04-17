# Runbook: DistinctSourceRatioDrop

**Severity:** warning

**What this means (one sentence):**
`distinct_source_count / source_count` dropped below 0.8 — the same source events are being cited repeatedly, inflating reinforcement counts without genuinely new distinct sources (the mem0 failure mode).

**Immediate action (do first):**
1. Which persona? Dashboard B → distinct-source ratio panel.
2. Is consolidator running faster than ingest? Check `wiki_v3_consolidator_lag_seconds` — near zero + dropping ratio = consolidator reprocessing.
3. Sample the inflated neurons:
   ```sql
   SELECT id, content, source_count, distinct_source_count, source_event_ids
   FROM neurons
   WHERE persona_id = ? AND superseded_at IS NULL
   ORDER BY (CAST(source_count AS REAL) / distinct_source_count) DESC
   LIMIT 20;
   ```

**Diagnostic steps:**
- For each inflated neuron, manually count distinct event IDs in `source_event_ids`. Does it match `distinct_source_count`?
- If mismatch, the counter update path is broken. Check consolidator for code that increments `source_count` without checking distinctness.
- Look for retry loops: ingest idempotency → if an extraction retry is creating new provenance edges for the same event, that inflates.

**Common causes, most to least likely:**
1. **Extractor rerun on same events without idempotency guard.** Fix: verify consolidator skips already-extracted events.
2. **Misconfigured reinforcement on retrieval traces** (Phase 2 LTP). Fix: retrieval reinforcement should update `fire_count` (decay only), never `distinct_source_count`.
3. **Active == shadow prompt** both producing neurons. Fix: only active writes neurons; shadow is comparison-only.
4. **Bug in `_increment_distinct()` helper.** Fix: audit the counter update function.

**Remediation:**
Trigger the repair healer to recount:
```bash
uv run memory-engine heal --repair distinct-count --persona <slug>
```
This recomputes `distinct_source_count` from `source_event_ids` for all non-superseded neurons.

**Escalation:**
If the ratio stays below 0.8 after remediation, file a blueprint DRIFT entry and escalate — this hits rule 4/15.

**Related:**
- docs/blueprint/02_v0.1.md §4.3 (distinct-source discipline)
