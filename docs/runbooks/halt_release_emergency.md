# Runbook: Emergency halt release

> When `memory-engine halt release` doesn't work because the halt-detection mechanism itself is broken. Last-resort procedure.

## First: try the normal release

Do not use this runbook until you've tried:

```bash
uv run memory-engine halt release --reason "..."
```

If that command returns "engine is not halted" but ingest still returns 503, or if it returns "halt released" but ingest is still blocked, then the detection mechanism is broken and you need this procedure.

## What could be broken

The halt state is computed from events. `is_halted()` queries:

```sql
SELECT count(*) FROM events
WHERE type = 'halted'
  AND NOT EXISTS (
    SELECT 1 FROM events e2
    WHERE e2.type = 'halt_released'
      AND e2.recorded_at > events.recorded_at
  );
```

It returns > 0 when halted. Broken scenarios:

- A bug in the query (unlikely; covered by tests).
- Clock skew on the DB host producing a `recorded_at` for the release event that's BEFORE the halt event (possible on systems with no NTP).
- Manual database edits that left the event log inconsistent.
- A newer `halted` event that the release didn't supersede (two halts, one release).

## Step 1: Verify the event log state

```bash
sqlite3 data/engine.db <<SQL
SELECT id, type, recorded_at, payload
FROM events
WHERE type IN ('halted', 'halt_released')
ORDER BY recorded_at DESC
LIMIT 10;
SQL
```

What you want to see: for every `halted` event, a LATER `halt_released` event. If there's a `halted` without a subsequent `halt_released`, or with a `halt_released` that has an earlier `recorded_at`, the state is inconsistent.

## Step 2: Force-release via CLI

```bash
uv run memory-engine halt release --force --reason "<specific explanation>"
```

The `--force` flag:
- Inserts a `halt_released` event with a timestamp 1 second after the latest `halted` event (not `now()`) to guarantee it supersedes.
- Emits an `operator_action` event recording the use of force.
- Requires a reason; the reason becomes part of both events.

Use this when the clock-skew or manual-edit issue is confirmed.

## Step 3: If step 2 fails

If `memory-engine halt release --force` itself errors, the engine binary may be corrupted. Fall back to direct SQL:

```bash
# DANGEROUS — bypasses all engine logic. Only under incident conditions.
sqlite3 data/engine.db <<SQL
-- Find the latest halted event
SELECT id, recorded_at FROM events
WHERE type = 'halted'
ORDER BY recorded_at DESC LIMIT 1;
SQL
```

Take note of that `recorded_at`. Then insert a release event with a later timestamp:

```bash
# Replace <LATEST_HALTED_AT> with the value from above, bumped by 1 second
sqlite3 data/engine.db <<SQL
INSERT INTO events (persona_id, type, scope, content_hash, payload, signature)
VALUES (
  0,
  'halt_released',
  'private',
  '',
  json('{"reason":"emergency manual release: engine mechanism broken","operator":"<your_handle>","at":"<LATEST_HALTED_AT+1s>"}'),
  ''
);
SQL
```

This is a direct DB write that bypasses signature verification and the normal CLI. It should ONLY be done:
- After exhausting steps 1 and 2.
- With a written incident report ready to file.
- On a machine that's offline from the network long enough to prevent any racing webhook.

## Step 4: Restart the engine

```bash
systemctl restart memory-engine    # or your equivalent
```

The in-memory halt cache (if any) refreshes on restart. Verify:

```bash
curl -s http://localhost:4000/metrics | grep memory_engine_halt_state
# Expect: memory_engine_halt_state 0
```

And test ingest:

```bash
# Send a test event via the adapter you trust
```

## Step 5: Write the incident report

Any use of this runbook requires an incident report at `docs/runbooks/incidents/YYYY-MM-DD-emergency-halt-release.md`. Include:

- What was the original halt reason?
- Why did the normal release fail?
- Which step did you use to escape?
- What evidence suggests the data is still consistent post-release?
- What prevention measure are you adding?

Prevention measures to consider:
- NTP configuration on the host.
- Automated test that detects clock skew at startup.
- An invariant that flags duplicate `halted` events without matching `halt_released`.

## When NOT to use this runbook

- The halt reason is a real critical violation. Fix the root cause first; release normally once it's resolved.
- You want to silence a noisy alert. The alert is right; the engine is halted.
- You're under time pressure from users. A wrong manual release can produce data inconsistencies that haunt the system for years. A 30-minute investigation is cheaper than a corrupted database.
