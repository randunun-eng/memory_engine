# Phase 6 — Blocking Gaps

> **Status:** Blocked on Phase 5. May be pursued in parallel with Phase 5's final weeks because schema additions (`prompt_comparison_daily`, `retrievals`) share plumbing.
>
> **Duration:** 4 weeks (half-time solo).
>
> **Acceptance criterion:** All three gaps from `docs/blueprint/08_blocking_gaps_closure.md` are closed. Dashboards render the 18 operational metrics live. A monthly backup restore drill completes under the 2-hour RTO. Prompts can be shadow-tested and promoted via CLI with the documented rollback path.

---

## Goal

The system becomes operable under production conditions. You can see what it's doing, you can recover if it breaks, and you can change prompts without redeploys. The closure document in `docs/blueprint/08_blocking_gaps_closure.md` is the authoritative spec; this phase document is the execution guide.

Phase 6 is the least thrilling phase to build and the most thrilling to have built. Skipping it makes every subsequent decision blind.

---

## Prerequisites

- Phase 5 complete. The adapter is in production or close to it.
- Access to a Grafana + Prometheus stack, or equivalent.
- An offsite storage target for backups (S3, B2, Wasabi, Hetzner Storage Box — anywhere that's not the primary).
- `age` installed for backup encryption.

---

## Schema changes

Migration 006. See `docs/SCHEMA.md` → Migration 006.

- `retrievals` — durable retrieval trace storage, separate from the event log.
- `prompt_comparison_daily` — aggregated shadow harness results.

Plus views: `active_neurons`, `persona_stats` (referenced by the dashboards).

---

## Gap 1 — Observability

### File manifest

- `src/memory_engine/observability/__init__.py`
- `src/memory_engine/observability/metrics.py` — Prometheus counters, histograms, gauges.
- `src/memory_engine/observability/logging.py` — structured JSON logger.
- `src/memory_engine/observability/tracing.py` — OpenTelemetry spans for the full request path (optional; off by default).
- `src/memory_engine/http/routes/metrics.py` — `/metrics` endpoint for Prometheus scrape.

### Metrics (18 total)

```python
# Counters
memory_engine_events_total{persona, type, scope}
memory_engine_neurons_total{persona, tier, kind}
memory_engine_recalls_total{persona, lens}
memory_engine_grounding_verdicts_total{persona, verdict}
memory_engine_quarantine_entries_total{persona, reason}
memory_engine_outbound_blocked_total{persona, stage, rule_id}
memory_engine_invariant_violations_total{persona, invariant_name, severity}
memory_engine_llm_calls_total{persona, site, cache}
memory_engine_halt_transitions_total{direction}

# Histograms
memory_engine_recall_latency_ms{persona, lens}
memory_engine_consolidator_run_duration_ms
memory_engine_healer_run_duration_ms
memory_engine_llm_call_latency_ms{site}

# Gauges
memory_engine_working_memory_size{persona}
memory_engine_active_neurons{persona, tier}
memory_engine_healing_unresolved{persona, severity}
memory_engine_halt_state{persona}
memory_engine_llm_cost_usd_total{persona}
```

Every metric is labeled; label cardinality is bounded. `persona` has at most ~10 values in foreseeable deployment. `site`, `tier`, `kind`, `lens`, `verdict`, `severity`, `invariant_name`, `stage` are all from enums. No unbounded labels.

### Dashboards

Three Grafana dashboards in `dashboards/`:

1. **`memory_engine_operations.json`** — halt state, ingest rate, recall rate, p50/p99 latencies, outbound blocked rate, quarantine backlog. Primary operator view.
2. **`memory_engine_quality.json`** — grounding accept/reject ratio, invariant violation trends, identity drift flags, LLM cost per persona, cache hit rate.
3. **`memory_engine_health.json`** — healer run time trend, consolidator run time trend, working memory size trend, active neuron count trend, DB size growth.

Dashboard JSON exports live in the repo under `dashboards/`. Load into Grafana with folder-level import.

### Alert rules

Prometheus alerting rules in `dashboards/alerts.yaml`. 12 rules, each with a runbook link:

```yaml
groups:
  - name: memory_engine.critical
    rules:
      - alert: MemoryEngineHalted
        expr: memory_engine_halt_state == 1
        for: 30s
        annotations:
          summary: "memory_engine {{ $labels.persona }} is halted"
          runbook: "https://github.com/randunun-eng/memory_engine/blob/main/docs/runbooks/halt_investigation.md"

      - alert: InvariantViolationCritical
        expr: rate(memory_engine_invariant_violations_total{severity="critical"}[5m]) > 0
        for: 1m

      - alert: OutboundBlockedSpike
        expr: rate(memory_engine_outbound_blocked_total[10m]) > 1
        for: 5m

      # ... 9 more
```

Each alert has a corresponding runbook at `docs/runbooks/<alert_slug>.md`. Template:

```markdown
# Runbook: <alert name>

## What triggered this
<description>

## Check first
1. <command>
2. <command>

## Common causes
- <cause>: <resolution>

## Escalation
If unresolved in 15 min, <action>.
```

### Structured logs

Every module uses `memory_engine.observability.logging.log()`:

```python
def log(*, event: str, **fields: Any) -> None:
    """Emit a structured log line.

    Required: event (snake_case identifier).
    All other fields become JSON keys. Timestamps and module info added automatically.
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "module": _caller_module(),
        **fields,
    }
    logger.info(orjson.dumps(record).decode("utf-8"))
```

CI test: every log call is structured (no `logger.info("string with %s", value)`). Enforced by a ruff custom rule or a simple AST check.

---

## Gap 2 — Backup & DR

### File manifest

- `bin/backup.sh` — SQLite backup with age encryption.
- `bin/backup_pg.sh` — Postgres pg_dump variant.
- `bin/restore.sh` — restore from encrypted artifact.
- `bin/verify_backup.sh` — weekly integrity check.
- `docs/runbooks/backup_drill.md` — monthly procedure.
- `docs/runbooks/disaster_recovery.md` — full RTO path.

### Backup procedure

```bash
# bin/backup.sh
#!/usr/bin/env bash
set -euo pipefail

PERSONA_SLUG="${1:?persona slug required}"
DEST="${MEMORY_ENGINE_BACKUP_DEST:?MEMORY_ENGINE_BACKUP_DEST env var required}"
RECIPIENT="${MEMORY_ENGINE_BACKUP_RECIPIENT:?MEMORY_ENGINE_BACKUP_RECIPIENT env var required}"

TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
STAGE=$(mktemp -d)
trap "rm -rf $STAGE" EXIT

# 1. Quiesce writes briefly: use SQLite online backup API
sqlite3 data/engine.db ".backup $STAGE/engine.db"

# 2. Bundle data directory (media, vault state, identity docs)
tar -cf $STAGE/data.tar data/media data/identity

# 3. Compute checksum of everything
(cd $STAGE && sha256sum * > manifest.sha256)

# 4. Package
tar -cf $STAGE/bundle.tar -C $STAGE engine.db data.tar manifest.sha256

# 5. Encrypt with age
age -r "$RECIPIENT" -o "$DEST/${PERSONA_SLUG}_${TS}.tar.age" "$STAGE/bundle.tar"

# 6. Verify offsite sync
offsite_sync_status=$("$(dirname "$0")/offsite_sync.sh" "$DEST/${PERSONA_SLUG}_${TS}.tar.age")
if [[ "$offsite_sync_status" != "ok" ]]; then
  echo "WARNING: offsite sync failed" >&2
  exit 1
fi

echo "Backup complete: ${PERSONA_SLUG}_${TS}.tar.age"
```

### Schedule

Cron (or systemd timer):

```
0 */6 * * * /opt/memory_engine/bin/backup.sh sales_twin        # every 6h
0 0 * * * /opt/memory_engine/bin/backup.sh sales_twin --daily  # daily full
0 0 * * 0 /opt/memory_engine/bin/verify_backup.sh              # weekly integrity
```

### Recovery targets

- **RPO** (recovery point objective): ≤ 5 minutes. Acceptable data loss = most recent 5 minutes of events.
- **RTO** (recovery time objective): ≤ 2 hours. Time from "disaster" to "service restored."

5-minute RPO requires either WAL shipping (aggressive) or 5-minute incremental backups. Phase 6 goes with 6-hour full backups + WAL archival for the last 6-hour window, combined RPO ≈ 5 minutes for the last window and 6 hours at worst.

### Drill

Monthly, documented in `docs/runbooks/backup_drill.md`:

1. Pick a random backup from the last 30 days.
2. Provision a fresh VM.
3. Run `bin/restore.sh <backup-file>`.
4. Run `memory-engine doctor` → must pass.
5. Run `memory-engine db status` → must match expected migration chain.
6. Spot-check: sample 10 event IDs, verify content_hash matches the original (proves no corruption).
7. Record drill results in `docs/runbooks/drills/YYYY-MM.md`.

Drill failure = production incident, same priority as a service outage.

---

## Gap 3 — Prompt Versioning

### File manifest

- `src/memory_engine/policy/registry.py` — expanded from Phase 2 skeleton.
- `src/memory_engine/policy/shadow.py` — dual-call shadow harness.
- `src/memory_engine/policy/promotion.py` — promote, rollback, traffic shifting.
- `src/memory_engine/cli/prompt.py` — expanded CLI.

### Registry behavior

Prompt templates live in the `prompt_templates` table (Phase 2 schema). Phase 6 makes the registry:
- Hot-reload on signal (`SIGHUP` or CLI-triggered).
- Surface active vs shadow via CLI.
- Enforce: exactly one active version per site at all times.

```python
class PromptRegistry:
    def __init__(self, conn_factory):
        self._conn_factory = conn_factory
        self._active_cache: dict[str, PromptVersion] = {}
        self._shadow_cache: dict[str, tuple[PromptVersion, float]] = {}  # (version, traffic_pct)
        self._reload_lock = asyncio.Lock()

    async def active(self, site: str) -> PromptVersion:
        if site in self._active_cache:
            return self._active_cache[site]
        # Fallback to DB
        await self.reload()
        return self._active_cache[site]

    async def reload(self) -> None:
        async with self._reload_lock:
            # Fetch all active and shadow rows
            ...
            self._active_cache = new_active
            self._shadow_cache = new_shadow

    def shadow_picks(self, site: str, rng: random.Random) -> PromptVersion | None:
        if site not in self._shadow_cache:
            return None
        version, pct = self._shadow_cache[site]
        if rng.random() < pct:
            return version
        return None
```

### Shadow harness

Every `dispatch()` call with a shadow-configured site runs twice: active and shadow. Shadow result is logged but not returned. Cache: only active's result is cached (shadow gets no cache to avoid skewing comparisons).

```python
async def dispatch(site: str, **kwargs) -> LLMResult:
    active_version = await registry.active(site)
    shadow_version = registry.shadow_picks(site, _rng)

    # Run active (main path)
    active_result = await _run_call(active_version, **kwargs)

    # Run shadow asynchronously (don't block caller)
    if shadow_version is not None:
        asyncio.create_task(_run_shadow(site, shadow_version, active_result, **kwargs))

    return active_result


async def _run_shadow(site, shadow_version, active_result, **kwargs):
    try:
        shadow_result = await _run_call(shadow_version, **kwargs)
        await log_comparison(site, active_result, shadow_result)
    except Exception:
        logger.exception("shadow_harness_failed", extra={"site": site})
```

Shadow traffic defaults to 10%. Configurable per site.

### Cost discount

Shadow calls run at a reduced model tier when possible. If the active uses Claude Opus, shadow might use Claude Haiku — 85% cheaper. Config per site:

```python
@dataclass
class SiteShadowConfig:
    traffic_pct: float = 0.10
    model_override: str | None = None   # if set, shadow uses this instead of active's model
```

### Promotion CLI

```bash
# Seed a new prompt version
memory-engine prompt add extract_entities v1_0_1 \
  --template-file prompts/extract_entities.v1_0_1.md \
  --notes "Tightened injection-defensive framing"

# List versions
memory-engine prompt list extract_entities
# Output:
#   extract_entities:
#     v1_0_0  ACTIVE   (created 2026-03-01 by nadeeshan)
#     v1_0_1  shadow   (10% traffic, since 2026-04-10)

# Start shadow
memory-engine prompt shadow extract_entities v1_0_1 --traffic 0.10

# After 48 hours of shadow data, compare
memory-engine prompt compare extract_entities v1_0_0 v1_0_1 --days 2
# Output:
#   active (v1_0_0):   n=1342, accept_rate=0.68, p50_lat=1200ms
#   shadow (v1_0_1):   n=134,  accept_rate=0.74, p50_lat=1100ms
#   delta: +0.06 accept, -100ms p50
#   recommendation: PROMOTE

# Promote
memory-engine prompt promote extract_entities v1_0_1 --reason "improved accept rate in shadow"

# Rollback if needed
memory-engine prompt rollback extract_entities --reason "accept rate degraded in production"
# This restores the previous active version as active.
```

### Audit trail

Every promote/rollback emits an event: `prompt_promoted` or `prompt_rolled_back`. Payload includes from/to versions, reason, operator, comparison metrics if any. Events are immutable (rule 1) so the audit trail can't be edited.

---

## Tests

### Integration (tests/integration/test_phase6.py)

```
# Gap 1
test_metrics_endpoint_serves_prometheus_format
test_structured_log_has_required_fields
test_dashboard_json_files_are_valid_grafana

# Gap 2
test_backup_produces_encrypted_artifact       # no plaintext on disk
test_backup_artifact_decrypts_with_key
test_restore_from_backup_matches_source_hashes
test_verify_backup_detects_corruption
test_drill_runbook_is_not_stale               # timestamps < 45 days

# Gap 3
test_prompt_seed_creates_version
test_prompt_promote_updates_active_flag
test_prompt_rollback_restores_previous_active
test_shadow_harness_does_not_affect_active_result
test_shadow_comparison_aggregates_correctly
test_prompt_hot_reload_picks_up_new_version
```

### Invariants (tests/invariants/test_phase6.py)

```
test_exactly_one_active_prompt_per_site
test_prompt_change_emits_event                # audit trail
test_metrics_have_bounded_cardinality
test_halt_alert_fires_when_halted
test_backup_recipient_never_logged
```

---

## Out of scope for this phase

- Distributed tracing backends (Jaeger, Tempo). OpenTelemetry is optional; no distributed collector required.
- Metrics federation across multiple engines. Phase 6 assumes one engine.
- Automated prompt generation. Humans still write prompt versions.
- Vault master key rotation tooling (documented manually in `docs/runbooks/vault_rotation.md`).
- Multi-region DR (active-passive, active-active). Single-region backup-and-restore only.

---

## Common pitfalls

**Metric cardinality explosion.** Adding a label like `counterparty_id` to every metric produces thousands of series. Keep labels bounded. If you need per-counterparty data, query the DB; do not shove it into Prometheus.

**Backup encryption key loss.** Backups encrypted with age are unreadable without the recipient's private key. Store the key separately from the backups. Losing both means data loss. Test the key is accessible monthly during the drill.

**Shadow harness cost doubling.** Running every call twice doubles LLM cost. The 10% sampling mitigates; the model override also helps. Monitor LLM cost during shadow — if it spikes, you're running too much shadow traffic.

**Prompt hot reload race.** Between "reload triggered" and "cache refreshed," some calls may use old prompts. Acceptable because propagation is fast (< 1s). Do not reload in the middle of a request.

**Alert fatigue.** 12 alerts feels sparse; watch for alert flapping. If an alert fires > 10 times in a week without action, the threshold is wrong. Tune.

**Dashboard drift.** Grafana dashboards are easy to edit in-place. The JSON export becomes stale. Either disable the in-UI editing or commit dashboard changes back to the repo on every change. Phase 6 goes with the second.

**Restore drill skipped.** The drill is tedious. That's the point. Skip it and you find out about backup corruption during a real disaster. Do it monthly; record results; put drill failure on the same priority as outage.

**Audit trail gaps.** Every promote/rollback emits an event — but if the code path fails *before* the event, the audit trail misses the change. Use "event-first" semantics: emit the event first, then execute the change. Failure to emit = failure to change.

---

## When Phase 6 closes

Tag: `git tag phase-6-complete`. Update `CLAUDE.md` §8.

Commit message: `feat(phase6): observability, backup/DR, prompt versioning`.
