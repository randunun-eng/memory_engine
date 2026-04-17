# Runbook: LLMSpendRateHigh

**Severity:** warning

**What this means (one sentence):**
Projected monthly LLM spend at current rate exceeds 150% of `monthly_budget_usd` — cost is about to blow the budget.

**Immediate action (do first):**
1. Dashboard A → LLM spend tile. Which sites dominate? Check `wiki_v3_llm_cost_usd_total` by `site` label.
2. Check cache hit ratio: `wiki_v3_llm_cache_hit_ratio`. If unusually low, cache bug. If normal, genuine volume.
3. Ingest rate: has traffic spiked?

**Diagnostic steps:**
- Per-site breakdown:
  ```promql
  sum by (site) (rate(wiki_v3_llm_cost_usd_total[1h]))
  ```
- Per-model breakdown (if multi-model). Is something routing to a more expensive model?
- Per-persona breakdown. Is one persona consuming disproportionately?
- Check recent prompt-template changes — a longer prompt costs more per call.

**Common causes, most to least likely:**
1. **Prompt cache disabled or poisoned.** Fix: check cache is keyed on `(site, prompt_hash, input_hash, persona_id)`. If poisoned, clear.
2. **New site/prompt with no cache warmup.** Fix: wait out warmup or disable the new site temporarily.
3. **Traffic spike (more users, more messages).** Fix: this is a revenue problem, not a bug. Raise budget or enable tier-based routing.
4. **Misrouted to expensive model** (e.g. fell back to GPT-4 instead of local Ollama). Fix: verify `config/litellm.yaml` routing.
5. **Shadow prompt running at 100% traffic by mistake.** Fix: set `shadow_traffic_pct = 0.1` or lower.

**Immediate containment:**
Pause the most expensive site via CLI:
```bash
uv run memory-engine prompt disable <site>
# Alternative: lower shadow traffic
uv run memory-engine prompt shadow-traffic <site> 0.01
```

**Escalation:**
If projected spend > 3x budget, halt the highest-consuming persona until root-cause understood.

**Related:**
- config/litellm.yaml (routing)
- docs/blueprint/03_v0.2.md §19 (cost model)
