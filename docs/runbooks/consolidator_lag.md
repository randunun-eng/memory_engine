# Runbook: ConsolidatorLagging

**Severity:** warning

**What this means (one sentence):**
Consolidator is processing events > 1 hour behind the ingest rate — events aren't becoming neurons on time; memory is silently degrading.

**Immediate action (do first):**
1. Check the lag metric: `wiki_v3_consolidator_lag_seconds` per persona.
2. Look at working memory depth: `SELECT persona_id, COUNT(*) FROM working_memory GROUP BY persona_id;` A big backlog here is the smoking gun.
3. Is the consolidator process alive? Logs should show `consolidator_pass_completed` events regularly.

**Diagnostic steps:**
- Recent consolidator errors: `SELECT * FROM healing_log WHERE invariant_name LIKE 'consolidator%' OR details LIKE '%consolidator%' ORDER BY detected_at DESC LIMIT 20;`
- LLM call latency: the consolidator blocks on extraction LLM calls. Is LLM slow? Check `wiki_v3_llm_call_latency_seconds`.
- CPU/memory pressure on the host.

**Common causes, most to least likely:**
1. **LLM backend slow or unreachable** (Ollama CPU-starved, LiteLLM proxy timing out). Fix: restart LLM backend, check GPU/CPU.
2. **Ingest spike overwhelming consolidator** (it processes sequentially by persona). Fix: wait for catch-up; consider batching extraction for high-volume personas.
3. **Consolidator task died silently.** Fix: restart engine; healer should catch the crash via its loop but a silent hang is possible.
4. **Prompt cache evicting hot entries.** Fix: increase cache size in config.

**Remediation:**
```bash
# Verify consolidator is running
uv run memory-engine consolidator status

# Manual trigger for a specific persona (drains working memory)
uv run memory-engine consolidator run --persona <slug>
```

**Escalation:**
If lag > 6 hours, promotion of events to semantic tier stops — recall quality degrades noticeably. Escalate for resource allocation.

**Related:**
- docs/blueprint/05_v0.4.md §4 (consolidation)
