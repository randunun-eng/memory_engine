# Runbook: halt investigation

> The `MemoryEngineHalted` alert fires when the engine has transitioned to read-only state. This runbook: find out why, decide what to do, release or keep halted.

## Alert context

```
alert: MemoryEngineHalted
expr: memory_engine_halt_state == 1
for: 30s
```

When this fires:
- `/v1/ingest` is returning 503.
- `/v1/recall` still works (reads are unaffected).
- No new events are being accepted.
- Outbound flow halts at the retrieval stage if it needs to log a `message_out` event.

## First steps (< 5 minutes)

### 1. Confirm the halt state

```bash
ssh <engine-host>
uv run memory-engine halt status
```

Expected output:

```
Halted: yes
Halted at: 2026-04-16T10:22:14Z
Reason: "3 critical invariant violations"
Released: no
```

### 2. Check the most recent healing log entries

```bash
uv run memory-engine heal recent --severity critical --limit 10
```

This shows the critical invariant violations that triggered the halt. Each entry has:
- `invariant_name` — which rule
- `persona_id` — which persona (0 = system-level)
- `details` — JSON payload with the specific offending row(s)

### 3. Check structured logs around the halt time

```bash
journalctl -u memory-engine --since "10 minutes ago" | grep -E '"event":"(invariant_checked|engine_halted)"'
```

Look at what the healer saw. Often the underlying problem is obvious — a neuron without citations, a scope value that's not in the enum, a corrupted `source_event_ids` JSON.

## Decide

### If the violation is transient / benign

E.g., a race condition between writer and healer that briefly shows a stale read. The healer's `_confirm_critical()` should catch this, but edge cases exist.

1. Re-run the healer manually: `uv run memory-engine heal run-once`.
2. Check if violations remain: `uv run memory-engine heal recent --severity critical --limit 10`.
3. If no current violations, release halt:

```bash
uv run memory-engine halt release --reason "spurious violation; healer re-run clean"
```

### If the violation is real and self-healing

Some violations are safe to repair automatically. Phase 3 ships repairs for:
- `stale_working_memory_entry` — prune the entry.
- `orphan_neurons_vec_row` — delete the vec row.

Run:

```bash
uv run memory-engine heal repair --auto --dry-run
# Review the proposed repairs
uv run memory-engine heal repair --auto
# Actually apply them
```

Re-run healer to confirm, release halt.

### If the violation is real and serious

E.g., a neuron exists with no citations (rule 14 breach) — means the consolidator's extraction produced a bad row, OR somebody wrote directly to the DB bypassing the engine.

1. Do NOT release halt immediately.
2. Investigate: how did the bad row get there?
   - Check for direct SQL writes: `journalctl -u memory-engine --since "1h ago" | grep -i "UPDATE\|INSERT\|DELETE"` for any unusual activity.
   - Check application deployments: was a new version deployed recently? Rollback candidate.
   - Check the event log for `operator_action` events that might explain.
3. Fix the root cause. Typically means reverting a code change or patching a bug.
4. Once root cause is fixed, clean up the bad row. For most cases this means marking affected neurons as superseded (not deleting — rule 2 says derived state is disposable, so superseding is enough).
5. Re-run healer, confirm clean, release halt.

## Release halt

```bash
uv run memory-engine halt release --reason "<specific explanation of what you did>"
```

The reason becomes part of the `halt_released` event payload — auditable. Be specific; "fixed it" is not acceptable.

## Emergency override (do not use casually)

If the halt mechanism itself is broken (e.g., bug in `is_halted()` that won't recognize a `halt_released` event), you can emergency-release directly:

```bash
# DANGEROUS — bypasses normal release flow
uv run memory-engine halt release --force --reason "..."
```

The `--force` flag inserts a `halt_released` event even if the halt detection says no halt is active. Reserved for broken-mechanism scenarios. Document in `docs/runbooks/incidents/` when used.

## Post-incident

After any halt that lasted more than 10 minutes:
1. Write an incident report at `docs/runbooks/incidents/YYYY-MM-DD-<slug>.md`.
2. Add an invariant test that would have caught the root cause earlier, if possible.
3. Review in the next retrospective.

Template for the incident report:

```markdown
# <YYYY-MM-DD> — <short description>

## What happened
## Detection (how did we find out)
## Impact (duration, affected personas, any data issues)
## Root cause
## Resolution
## Timeline
## Contributing factors
## Action items (each with an owner and a target date)
```
