# Runbook: Embedder dimension change

> Upgrading to an embedder with different dimensions (e.g., 384 → 768) requires recreating the `neurons_vec` virtual table and re-embedding all existing neurons. This is a coordinated, one-time operation per rotation.

## When to do this

- Moving from `all-MiniLM-L6-v2` (384) to `bge-base-en-v1.5` (768) for quality.
- Moving to a multilingual embedder with different dimensions.
- Consolidating across rotations where different embedder_revs accumulated.

## When NOT to do this

- For same-dimension embedder updates. Use the simpler re-embed-in-place procedure from ADR 0006.
- If you're unsure the new embedder is better. Shadow-test first with a subset of queries.

## Prerequisites

- New embedder model available locally (downloaded to sentence-transformers cache).
- Confirmed retrieval improvement in shadow evaluation: new embedder's MRR@10 on your eval baseline exceeds current by a meaningful margin.
- Downtime window: ~(neuron count) * (embedding latency) / batch_size. Example: 100k neurons at 5ms each, batch 32 → ~5 minutes of CPU time plus overhead. Plan for 20 minutes on a typical VM.
- Fresh backup taken immediately before starting.

## Procedure

### 1. Halt the engine

```bash
uv run memory-engine halt force --reason "embedder dimension change"
```

### 2. Take a snapshot backup

```bash
/opt/memory_engine/bin/backup.sh <persona_slug>
```

If anything goes wrong, this is your rollback point.

### 3. Drop and recreate the vector table

```bash
sqlite3 data/engine.db <<SQL
DROP TABLE neurons_vec;
CREATE VIRTUAL TABLE neurons_vec USING vec0(
  neuron_id INTEGER PRIMARY KEY,
  embedding FLOAT[768]     -- NEW DIMENSION
);
SQL
```

The `neurons_vec` table is derived state (rule 2); dropping it is safe. Neurons themselves are untouched.

### 4. Update config

```bash
# config/default.toml (or environment)
sed -i 's|^dimensions = 384$|dimensions = 768|' config/default.toml
sed -i 's|^model = .*|model = "BAAI/bge-base-en-v1.5"|' config/default.toml
sed -i 's|^revision = .*|revision = "bge-base-en-1.5-2026-04"|' config/default.toml
```

### 5. Run the reindex

```bash
uv run memory-engine embed reindex \
  --to-revision bge-base-en-1.5-2026-04 \
  --batch-size 32 \
  --progress
```

The reindex tool:
- Loads the new embedder.
- Iterates every active neuron (`superseded_at IS NULL`).
- Re-embeds the neuron's content.
- Inserts into `neurons_vec` and updates `neurons.embedder_rev`.
- Reports progress.

Expected output:

```
Loading BAAI/bge-base-en-v1.5... OK
Active neurons: 98,432
Re-embedding... [#####################] 98432/98432 (100%) - 4m 52s
Verification: all active neurons have matching neurons_vec rows.
```

### 6. Verify

```bash
# Counts should match
sqlite3 data/engine.db "
  SELECT
    (SELECT count(*) FROM neurons WHERE superseded_at IS NULL) AS neurons,
    (SELECT count(*) FROM neurons_vec) AS vec_rows;
"
# Expect: neurons = vec_rows

# Sample retrieval
curl -sX POST http://localhost:4000/v1/recall \
  -H 'content-type: application/json' \
  -d '{"persona_slug":"<slug>","query":"<test query>","lens":"self","top_k":5}'
# Expect: results, same-ish ranking as before (not identical — different embedder)
```

### 7. Release halt

```bash
uv run memory-engine halt release --reason "embedder rotated to bge-base-en-v1.5; reindex complete"
```

### 8. Run the eval suite

```bash
uv run pytest tests/eval -v --eval
```

Compare MRR@10 against the baseline in `tests/eval/baseline_phase7.yaml` (or whichever is current). Record the delta in `docs/blueprint/DRIFT.md`:

```markdown
| 2026-04-16 | embeddings | rotated all-MiniLM-L6-v2 → bge-base-en-v1.5 | MRR@10 improved from 0.64 to 0.71 on 50-query baseline | Accepted |
```

## Rollback

If the new embedder is worse in production:

1. Halt.
2. Restore the backup from step 2.
3. Release halt.
4. The old embedder and dimension are restored as if the rotation never happened.

Losing time is better than losing quality silently.

## Subtleties

**Old and new revs during transition.** While the reindex runs, neurons may have a mix of old and new `embedder_rev`. Retrieval filters by the currently-loaded embedder's revision, so mixed state during reindex means queries return only what's been migrated so far. This is why we halt during the rotation.

**Superseded neurons.** The reindex skips `superseded_at IS NOT NULL` rows. They stay on the old revision. They don't participate in retrieval (superseded), so this is fine. If you later un-supersede one, re-embed it manually.

**Quarantine entries.** Quarantined candidates are not re-embedded; they never had `neurons_vec` rows to begin with. No action needed.

**Synapses.** Unaffected. Synapses reference neuron IDs, not embeddings.

**Cost.** Re-embedding 100k neurons with a local embedder: free. With a hosted embedder (OpenAI `text-embedding-3-small`): 100k * ~500 tokens avg * $0.02/1M tokens = ~$1. Still cheap, but budget if you have millions.

## Documenting the rotation

Append to `docs/runbooks/rotations.md`:

```
- 2026-04-16: embedder rotated: all-MiniLM-L6-v2 (384) → bge-base-en-v1.5 (768). Reindex duration: 4m 52s. MRR@10 improvement: +0.07. Operator: <handle>.
```
