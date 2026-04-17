# Runbook: RecallLatencyP99High

**Severity:** warning

**What this means (one sentence):**
Recall p99 latency > 1.5s for 15+ minutes — users feel slow queries; retrieval budget is blown.

**Immediate action (do first):**
1. Dashboard A → recall latency panel. Is this gradual or sudden?
2. Which stream is slow? Check `wiki_v3_recall_degraded_total` by stream label:
   - `vector` slow → sqlite-vec extension issue or embedder latency
   - `bm25` slow → rank-bm25 rebuild on stale index
   - `graph` slow → large neuron count + networkx memory pressure
3. Current neuron count: `SELECT COUNT(*) FROM neurons WHERE superseded_at IS NULL;`

**Diagnostic steps:**
- EXPLAIN QUERY PLAN on a representative recall. Is it using the expected indexes?
- Check `ANALYZE` statistics freshness: `SELECT * FROM sqlite_stat1 WHERE tbl = 'neurons';`
- Recent neuron growth: compare last 24h neurons added vs prior 24h.
- Vector index size: `SELECT COUNT(*) FROM neurons_vec;`

**Common causes, most to least likely:**
1. **Neuron count crossed a threshold where the naive approach scales badly.** Fix: run `ANALYZE`; verify indexes present via `SELECT name FROM sqlite_master WHERE type='index';`
2. **BM25 index rebuild happens on every query** (shouldn't — should be cached). Fix: verify the BM25 cache is warm after restart.
3. **sqlite-vec extension not loaded.** Fix: see `sqlite_vec_install.md`. Vector search falls back to full scan otherwise.
4. **Disk I/O saturated** (check `iostat`). Fix: provision faster disk or tune SQLite cache_size.

**Remediation:**
```bash
# Rebuild statistics
sqlite3 data/engine.db "ANALYZE;"

# Warm the BM25 cache (retrieval module handles this on first call)
# If deployed behind a systemd unit, restart triggers warmup
sudo systemctl restart memory-engine
```

**Escalation:**
If latency stays elevated after `ANALYZE` + restart, this is a scale problem, not a tuning problem. Plan Postgres migration (blueprint §6 mentions the path).

**Related:**
- tests/perf/test_retrieval_latency.py (baseline)
- runbooks/sqlite_vec_install.md
