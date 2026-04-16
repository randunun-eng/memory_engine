# Wiki v3 — Blocking Gaps Closure

> Specifications for the three gaps that block first production deployment.
> Closes §5.1 from the synthesis document. Approximately 3–4 weeks of work total.
> Not architectural changes; operational readiness.

---

## Gap 1 — Observability Runbook

### 1.1 What exists already (v0.2 §18, §19)

- Prometheus-compatible `/metrics` endpoint
- Structured JSON logs
- Health and readiness probes
- `wiki-v3 doctor` CLI for invariant reports

### 1.2 What is missing

Three concrete artifacts that together let an operator know the system is healthy without reading logs:

1. Dashboard definitions (Grafana JSON)
2. Alert rules with explicit thresholds
3. One-page runbooks per alert

Without these, problems are discovered by users, not by monitoring. That is the failure mode to eliminate.

### 1.3 Metric catalog

Every metric below has a label schema, a type, and a reason for existing. Metrics without a named failure mode they detect should be removed, not added.

| Metric | Type | Labels | Detects |
|---|---|---|---|
| `wiki_v3_events_appended_total` | counter | `persona, source, type` | Ingest rate and type mix |
| `wiki_v3_events_rejected_total` | counter | `persona, reason` | Ingest failures (signature, tombstone, rate limit) |
| `wiki_v3_ingest_latency_seconds` | histogram | `persona` | Hot-path latency budget (target p99 < 0.5s) |
| `wiki_v3_grounding_gate_verdict_total` | counter | `persona, verdict` | Grounding gate pass/fail/review rates |
| `wiki_v3_quarantine_depth` | gauge | `persona, table` | Quarantine growth (unreviewed rejects) |
| `wiki_v3_neurons_total` | gauge | `persona, kind, tier` | Memory growth per tier |
| `wiki_v3_distinct_source_ratio` | gauge | `persona` | `distinct_source_count / source_count` — low values mean echo inflation (mem0 failure mode) |
| `wiki_v3_recall_latency_seconds` | histogram | `persona, lens, streams` | Retrieval latency budget (target p50 < 0.15s, p99 < 0.8s) |
| `wiki_v3_recall_degraded_total` | counter | `persona, stream` | Which retrieval stream degraded (vector/bm25/graph) |
| `wiki_v3_invariant_check_total` | counter | `invariant, status` | Invariant pass/fail rates |
| `wiki_v3_invariant_violation_total` | counter | `invariant, severity` | Hard-fail privacy violations — must stay at 0 |
| `wiki_v3_persona_output_verdict_total` | counter | `persona, verdict` | Outbound approval/rejection rates |
| `wiki_v3_identity_flag_total` | counter | `persona, flag_type` | Identity drift signals (value contradiction, self-contradiction) |
| `wiki_v3_llm_cost_usd_total` | counter | `persona, site, model` | Running LLM spend |
| `wiki_v3_llm_cache_hit_ratio` | gauge | `site` | Prompt cache effectiveness |
| `wiki_v3_event_log_size_bytes` | gauge | `persona` | Event log growth — triggers snapshot cadence planning |
| `wiki_v3_mcp_auth_failures_total` | counter | `mcp_name, reason` | MCP compromise indicator |
| `wiki_v3_consolidator_lag_seconds` | gauge | `persona, phase` | Consolidator falling behind ingest rate |

### 1.4 Dashboards (Grafana JSON, three layouts)

Ship three Grafana dashboards in `dashboards/` of the repo.

**Dashboard A — Operations Overview (one screen, for on-call).**
Panels, left-to-right, top-to-bottom:
1. Hard invariant violations (24h) — single-stat, red if > 0, green if 0.
2. Ingest rate — time-series per persona.
3. Recall p50/p95/p99 — time-series.
4. Grounding gate pass rate — single-stat with sparkline.
5. Quarantine depth — single-stat per table.
6. Event log growth — time-series.
7. LLM spend (24h rolling) — single-stat with monthly projection.
8. MCP auth failures — time-series.

**Dashboard B — Memory Health (for weekly review).**
1. Neurons per tier per persona — time-series stacked.
2. Distinct-source ratio per persona — time-series. Red line at 0.8 (below means echo inflation).
3. Supersession rate — time-series.
4. Pruning rate — time-series.
5. Skill shadow-period outcomes — table.
6. Identity drift flags — heatmap by day.

**Dashboard C — Per-Persona Deep-Dive (for diagnosis).**
Parameterized by persona. All metrics above, filtered to that persona, plus:
1. Top 20 neurons by `fire_count` — table (for audit).
2. Top 20 counterparties by interaction volume — table.
3. Recent quarantine entries — table with reasons.

### 1.5 Alert rules

All alerts have: a severity, an SLO, a condition, and a runbook link. Alerts without runbooks are forbidden.

**Critical (page immediately, no auto-remediation):**

```yaml
- alert: HardInvariantViolation
  expr: increase(wiki_v3_invariant_violation_total{severity="critical"}[5m]) > 0
  severity: critical
  runbook: runbooks/hard_invariant.md
  description: "Privacy/leak/scope invariant halted the system. System is in read-only."

- alert: MCPAuthFailureSpike
  expr: rate(wiki_v3_mcp_auth_failures_total[5m]) > 0.5
  severity: critical
  runbook: runbooks/mcp_compromise.md
  description: "Sustained MCP authentication failures. Possible compromise or rotation issue."

- alert: EventLogStalled
  expr: rate(wiki_v3_events_appended_total[10m]) == 0 and up{job="wiki_v3"} == 1
  for: 10m
  severity: critical
  runbook: runbooks/ingest_stalled.md
  description: "Process healthy but no events appending."
```

**Warning (notify during business hours):**

```yaml
- alert: DistinctSourceRatioDrop
  expr: wiki_v3_distinct_source_ratio < 0.8
  for: 30m
  severity: warning
  runbook: runbooks/echo_inflation.md
  description: "Reinforcement-on-repeat inflating source_count. Possible extractor loop."

- alert: QuarantineDepthGrowing
  expr: wiki_v3_quarantine_depth > 100 and increase(wiki_v3_quarantine_depth[24h]) > 50
  severity: warning
  runbook: runbooks/quarantine_review.md
  description: "Quarantine queue growing without review."

- alert: GroundingGateRejectRateHigh
  expr: rate(wiki_v3_grounding_gate_verdict_total{verdict="reject"}[1h]) / rate(wiki_v3_grounding_gate_verdict_total[1h]) > 0.4
  severity: warning
  runbook: runbooks/extractor_quality.md
  description: "Grounding gate rejecting > 40%. Extractor prompt may be degraded."

- alert: RecallLatencyP99High
  expr: histogram_quantile(0.99, wiki_v3_recall_latency_seconds_bucket) > 1.5
  for: 15m
  severity: warning
  runbook: runbooks/retrieval_latency.md

- alert: LLMSpendRateHigh
  expr: rate(wiki_v3_llm_cost_usd_total[1h]) * 24 * 30 > (monthly_budget_usd * 1.5)
  severity: warning
  runbook: runbooks/cost_overrun.md
  description: "Projected spend exceeds 150% of configured budget."

- alert: ConsolidatorLagging
  expr: wiki_v3_consolidator_lag_seconds > 3600
  severity: warning
  runbook: runbooks/consolidator_lag.md
  description: "Consolidator falling behind ingest. Memory may not reach semantic tier in time."

- alert: IdentityFlagSpike
  expr: rate(wiki_v3_identity_flag_total[1h]) > 5
  severity: warning
  runbook: runbooks/identity_drift.md
  description: "Unusual number of identity-related flags. Investigate persona drift."
```

### 1.6 Runbook template

Every runbook follows this shape. One file per alert, one page maximum. If the runbook grows past one page, the alert is too broad.

```markdown
# Runbook: [alert name]

**Severity:** [critical | warning]

**What this means (one sentence):**
[Plain-language description]

**Immediate action (do first):**
1. [Step 1 — usually "check dashboard X"]
2. [Step 2 — usually "look for common cause Y"]

**Diagnostic steps (if immediate didn't resolve):**
- [Query or command 1 with expected output]
- [Query or command 2]
- [...]

**Common causes, most to least likely:**
1. [Cause 1 → fix]
2. [Cause 2 → fix]
3. [Cause 3 → fix]

**Escalation:**
[When to escalate, to whom, with what information]

**Related:**
[Other runbooks that often accompany this one]
```

### 1.7 Log structure

Every log line is JSON with these required fields:

```json
{
  "ts": "2026-04-16T10:23:45.123Z",
  "level": "info",
  "persona_id": 1,
  "request_id": "req_abc123",
  "module": "consolidator.grounding_gate",
  "event": "grounding_verdict",
  "verdict": "accepted",
  "neuron_candidate_hash": "...",
  "source_event_ids": [12345],
  "similarity_score": 0.73
}
```

Required: `ts`, `level`, `module`, `event`. Everything else is event-specific. The `event` field is the primary filter key — operators search by event, not by free text.

### 1.8 Acceptance criteria

- Three dashboard JSON files importable into Grafana 10+
- Alert rules in Prometheus format, each with a runbook link that resolves
- Runbooks: 12 files (one per alert), each under 200 lines
- Log structure enforced by a single logger module, not per-call discipline
- Operator can answer "is everything OK right now" in under 30 seconds on Dashboard A

### 1.9 Effort estimate

**1 week.** Breakdown:
- Metric instrumentation across existing modules: 2 days
- Grafana dashboards: 1 day
- Alert rules + threshold tuning: 1 day
- Runbook authoring: 1 day

---

## Gap 2 — Backup and Disaster Recovery

### 2.1 What exists already

- Event log is durable (SQLite WAL or Postgres WAL)
- Snapshots (v0.2 §20) reduce replay time
- Protective forgetting handles selective deletion

### 2.2 What is missing

Three things no deployment can responsibly go without:

1. A backup procedure with encryption at rest
2. Offsite replication (protects against local failures and ransomware)
3. Regular recovery drills with measured RTO/RPO

The blueprint mentions snapshots for fast replay but not for durability. These are different concerns.

### 2.3 Targets

| Metric | Target | Rationale |
|---|---|---|
| RPO (Recovery Point Objective) | ≤ 5 minutes | How much data can be lost in a disaster. 5min = last continuous backup. |
| RTO (Recovery Time Objective) | ≤ 2 hours | From disaster declaration to operational. 2h accommodates human decision time + restore. |
| Drill frequency | Monthly | Unexercised recovery procedures do not work when needed. |
| Backup retention | 30 daily + 12 monthly + 3 yearly | Covers accidental deletion (days), extended incidents (months), regulatory needs (years). |
| Backup encryption | Always on, customer-managed key | Backups at rest are high-value targets. |
| Backup location diversity | At least 2 (local + offsite) | Single-location backup is not a backup. |

Deployments with stricter regulatory requirements override these; these are the floor, not the ceiling.

### 2.4 Backup procedure

**SQLite deployment:**

```bash
# Online backup using SQLite .backup (doesn't require downtime)
sqlite3 /data/wiki.db ".backup '/backup/staging/wiki-$(date -u +%Y%m%d-%H%M%S).db'"

# Encrypt with age (symmetric, modern)
age -e -r "$WIKI_V3_BACKUP_RECIPIENT" \
  /backup/staging/wiki-*.db \
  > /backup/encrypted/wiki-$(date -u +%Y%m%d-%H%M%S).db.age

# Delete unencrypted staging
shred -u /backup/staging/wiki-*.db

# Replicate offsite (example: S3-compatible with server-side encryption disabled because already encrypted)
aws s3 cp /backup/encrypted/wiki-*.db.age "s3://wiki-v3-backups/$(hostname)/" --storage-class STANDARD_IA
```

**Postgres deployment:**

```bash
# Logical backup with compression
pg_dump --format=custom --compress=9 \
  --dbname="$DATABASE_URL" \
  --file=/backup/staging/wiki-$(date -u +%Y%m%d-%H%M%S).dump

# Encrypt, replicate, shred staging — same pattern as SQLite
```

**Continuous WAL archiving (for RPO < 5 min on Postgres):**

Postgres `archive_command` ships each completed WAL segment to encrypted storage. Point-in-time recovery works by restoring the last base backup + replaying WAL up to the target moment. Documented in `docs/backup.md`.

**What backup never includes:**
- The operator's unseal key for the vault. This is kept separately, on hardware or in a password manager. Losing the unseal key renders backups useless, which is intentional — stolen backups without the key are inert.
- Configuration secrets (LLM API keys, MCP tokens). Those live in the deployment environment; they are re-injected on restore, not restored.

### 2.5 Restore procedure

```bash
# 1. Stop wiki-v3 if running
systemctl stop wiki-v3

# 2. Fetch the backup
aws s3 cp "s3://wiki-v3-backups/$(hostname)/wiki-20260416-120000.db.age" /restore/

# 3. Decrypt
age -d -i ~/.age/wiki-backup-key.txt \
  /restore/wiki-20260416-120000.db.age \
  > /restore/wiki.db

# 4. Verify integrity before replacing production
sqlite3 /restore/wiki.db "PRAGMA integrity_check;"
# Must return "ok"

# 5. Run wiki-v3 self-check against the restored DB
wiki-v3 --db /restore/wiki.db doctor --no-repair
# Must pass all hard invariants

# 6. Replace production DB
mv /data/wiki.db /data/wiki.db.pre-restore
mv /restore/wiki.db /data/wiki.db

# 7. Restart, verify
systemctl start wiki-v3
curl -sf http://localhost:8080/health

# 8. Announce restore complete, document in incident log
```

**Restore time measured on every drill.** If the procedure takes longer than RTO, the procedure needs changes (parallelize, pre-stage, etc.).

### 2.6 Drill procedure

Monthly, on a schedule, on a non-production clone:

1. Provision a fresh environment matching production config.
2. Restore from the most recent encrypted backup.
3. Start wiki-v3 against restored data.
4. Run a scripted set of test queries whose expected results are known (same set as the eval baseline).
5. Measure: time to operational, query correctness, any data loss vs expected.
6. Document result in `drills/YYYY-MM-DD.md`.

If a drill fails or exceeds RTO, the next regular work priority is remediation — not new features.

### 2.7 Backup monitoring

New metrics for Gap 1's dashboard:

```yaml
wiki_v3_backup_last_success_seconds   # Time since last successful backup
wiki_v3_backup_size_bytes              # Backup artifact size
wiki_v3_backup_offsite_lag_seconds     # Delay between local backup and offsite replication
```

New alert:

```yaml
- alert: BackupStale
  expr: wiki_v3_backup_last_success_seconds > 3700  # 1 hour + 100s grace
  severity: critical
  runbook: runbooks/backup_stale.md
```

### 2.8 Acceptance criteria

- `bin/backup.sh` runs hourly via cron or systemd timer
- Backups encrypted with age/gpg, never stored in plaintext
- Offsite copies verified present by monitoring
- `bin/restore.sh` documented and tested
- Monthly drill scheduled and first drill completed before first production user

### 2.9 Effort estimate

**1 week.** Breakdown:
- Backup and encryption scripts: 2 days
- Offsite replication: 1 day
- Restore procedure and testing: 1 day
- First drill + documentation: 1 day

---

## Gap 3 — Prompt Template Versioning

### 3.1 What exists already

- Prompts as markdown files in `src/wiki_v3/llm/prompts/` (v0.0 §8)
- `prompt_template_rev` in cache key (v0.2 §19)

### 3.2 What is missing

Extraction prompts are load-bearing (v0.4 grounding gate, identity-aware extraction). They will change. Changing them currently means editing the file and restarting — no rollback path, no A/B, no way to know if the new prompt is better. A regressed prompt silently degrades grounding gate pass rates for days before anyone notices.

### 3.3 Schema

```sql
prompt_templates(
  id              BIGSERIAL PRIMARY KEY,
  site            TEXT NOT NULL,                  -- 'extract_entities' | 'classify_scope' | 'judge_contradiction' | ...
  version         TEXT NOT NULL,                  -- semver or git sha
  template_text   TEXT NOT NULL,
  parameters      JSONB NOT NULL,                 -- schema for variables the prompt expects
  created_at      TIMESTAMPTZ NOT NULL,
  created_by      TEXT NOT NULL,
  active          BOOLEAN NOT NULL DEFAULT FALSE, -- only one 'active' per site at a time
  notes           TEXT,
  UNIQUE (site, version)
);

CREATE UNIQUE INDEX prompt_templates_active_per_site
  ON prompt_templates (site) WHERE active = TRUE;
```

### 3.4 Loading behavior

On process start and every N minutes thereafter, the LLM client loads the active template per site. Hot reload means a prompt change takes effect within N minutes without restart. Default N = 5.

```python
# pseudocode
class PromptRegistry:
    def __init__(self, db, reload_interval=300):
        self.db = db
        self.cache = {}
        self.reload_interval = reload_interval

    def get(self, site):
        if site not in self.cache or self.cache[site].stale():
            self.cache[site] = self.db.fetch_active(site)
        return self.cache[site]
```

Every LLM call records the `prompt_template_id` used in its trace. A subsequent query can correlate "which prompt version produced which neurons" for root-cause analysis.

### 3.5 A/B harness

New concept: **shadow prompt**. A prompt marked `shadow` runs alongside the active prompt on a configurable fraction of events; both outputs are captured, the active one is used, and comparison is measured.

```sql
ALTER TABLE prompt_templates ADD COLUMN shadow BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prompt_templates ADD COLUMN shadow_traffic_pct REAL NOT NULL DEFAULT 0;
```

Execution flow:

```python
active = registry.get(site)
if active.has_shadow():
    shadow = registry.get_shadow(site)
    if random() < shadow.shadow_traffic_pct:
        # Run both
        active_out = llm.call(active.template, **inputs)
        shadow_out = llm.call(shadow.template, **inputs)
        # Log for offline comparison
        log_shadow_comparison(site, active.id, shadow.id, active_out, shadow_out, inputs)
        return active_out
    else:
        return llm.call(active.template, **inputs)
else:
    return llm.call(active.template, **inputs)
```

### 3.6 Comparison metrics

For each prompt site, a specific metric determines "better":

| Site | Primary metric | Secondary |
|---|---|---|
| `extract_entities` | Grounding gate pass rate | Neuron count per episode |
| `classify_scope` | Scope classification agreement with human label | Confidence calibration |
| `summarize_episode` | Faithfulness (does summary reflect episode content) — LLM-judged sample | Length |
| `judge_contradiction` | Agreement with human verdict on labeled pairs | Consistency across re-runs |
| `merge_content` | Content preservation score | Hallucination injection rate |

Metric computation runs on a daily batch job over the prior day's shadow comparison logs. Results written to `prompt_comparison_daily` table.

### 3.7 Promotion procedure

A shadow prompt is promoted to active only via an explicit `wiki-v3 prompt promote` CLI command:

```bash
$ wiki-v3 prompt status
SITE                 ACTIVE           SHADOW            TRAFFIC
extract_entities     v3.2.1 (14d)     v3.3.0-rc1 (3d)   10%

$ wiki-v3 prompt compare extract_entities v3.3.0-rc1
Comparison for extract_entities, 1400 shadow samples over 3 days:

Metric                          Active (v3.2.1)    Shadow (v3.3.0-rc1)    Delta
grounding_pass_rate             0.724              0.781                  +7.9%
neurons_per_episode (mean)      3.2                3.4                    +6.3%
llm_cost_per_episode (mean)     $0.0042            $0.0047                +11.9%

Verdict: shadow improves grounding at moderate cost increase.

$ wiki-v3 prompt promote extract_entities v3.3.0-rc1
This will:
- Deactivate v3.2.1
- Activate v3.3.0-rc1 at 100% traffic
- Emit a 'prompt_promoted' event to the log
Continue? [y/N] y
Promoted. Monitor dashboard for 24h to watch for regressions.
```

### 3.8 Rollback procedure

If a promoted prompt regresses, rollback is:

```bash
$ wiki-v3 prompt rollback extract_entities
Rolling back to v3.2.1 (previous active)...
Deactivating v3.3.0-rc1, reactivating v3.2.1.
Done. Emit a 'prompt_rolled_back' event with your reason.
Reason: > "Grounding pass rate dropped on production traffic, not caught in shadow."
Rollback recorded.
```

Rollback must be possible in under 60 seconds. It changes no data, only which template is active. Neurons produced under the regressed prompt stay, but the healer can be asked to rescreen them: `wiki-v3 heal rescreen-neurons --produced-under v3.3.0-rc1`.

### 3.9 Guardrails

- Only shadow prompts can have `active=false` and `shadow_traffic_pct > 0`.
- Promoting a prompt that has had fewer than 100 shadow samples requires `--force`.
- Promoting a prompt whose primary metric is worse than active requires `--accept-regression <reason>`.
- `wiki-v3 prompt promote` writes a `prompt_promoted` event to the event log. Audit trail is non-optional.

### 3.10 Acceptance criteria

- Prompt templates loaded from DB, not files, at runtime
- Hot reload works without process restart
- Shadow harness running with at least one real prompt under shadow testing
- Comparison table populated by daily batch
- Promote and rollback CLI commands documented and tested
- Every LLM call's trace records the prompt template version used

### 3.11 Effort estimate

**1–2 weeks.** Breakdown:
- Schema + loader + hot reload: 2 days
- Shadow harness: 2 days
- Comparison metrics + daily batch: 2 days
- CLI + docs: 1 day
- Seed existing prompts into DB as version 1.0.0: 1 day

---

## Summary

| Gap | Spec length | Effort | Blocking reason |
|---|---|---|---|
| 1 — Observability runbook | ~300 lines | 1 week | Without dashboards and runbooks, problems are discovered by users |
| 2 — Backup / DR | ~250 lines | 1 week | Without tested backups, disaster means data loss |
| 3 — Prompt versioning | ~300 lines | 1–2 weeks | Without this, extraction quality regresses invisibly |

**Total: 3–4 weeks of work.** Adding to v0.4's 20–26 week roadmap yields **23–30 weeks half-time solo** for a defensibly operable first production deployment. No architecture changes required; this is operational readiness only.

After this work, the blueprint is ready to build against. Not "perfect" — ready.

---

*End of blocking gaps closure.*
