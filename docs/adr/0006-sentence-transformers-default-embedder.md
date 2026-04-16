# 0006 — sentence-transformers `all-MiniLM-L6-v2` as default embedder

## Status

Accepted — 2026-04-16

## Context

Neuron vector embeddings power the vector stream in retrieval and the similarity check in the grounding gate. The embedder choice affects:
- Embedding dimension (storage per neuron and per `neurons_vec` row).
- Embedding quality (retrieval MRR@k).
- Inference latency (every new neuron and every retrieval query embeds text).
- Deployment footprint (model size on disk, memory).
- Cost model (local vs hosted).
- Multilingual coverage (depending on persona's counterparty languages).

We want a default that:
- Runs locally (no API dependency at the critical path).
- Fits comfortably on an ARM VM with 24 GB RAM.
- Has reasonable retrieval quality for English and common Romance/Germanic languages.
- Is ubiquitous enough that users can download it once and not worry.
- Supports CPU inference (our default VM has no GPU).

Candidates:

| Model | Dim | Size | CPU latency (1 doc) | Multilingual |
|---|---|---|---|---|
| `all-MiniLM-L6-v2` (sentence-transformers) | 384 | ~80 MB | ~5 ms | English-strong, limited multilingual |
| `all-mpnet-base-v2` | 768 | ~420 MB | ~15 ms | English-strong |
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | ~470 MB | ~10 ms | 50+ languages |
| `e5-small-v2` | 384 | ~130 MB | ~7 ms | English |
| `bge-small-en-v1.5` | 384 | ~130 MB | ~7 ms | English, high MTEB score |
| OpenAI `text-embedding-3-small` | 1536 | hosted | ~100 ms (network) | multi |
| Voyage `voyage-3-lite` | 512 | hosted | ~100 ms (network) | multi |

On MTEB (Massive Text Embedding Benchmark), `all-MiniLM-L6-v2` is not the top performer, but it's the best combination of small-and-widely-available. `bge-small-en-v1.5` beats it by a few MTEB points; `e5-small-v2` is similar. `all-MiniLM-L6-v2` wins on ubiquity — every embedding tutorial, RAG demo, vector-DB example uses it, so users arrive with it cached.

Hosted embedders (OpenAI, Voyage, Cohere) offer higher quality, but they add network dependency to hot paths and require API keys, defeating the "local by default" goal.

Phase 0–7 deployments for a single operator will have English-heavy counterparties (the operator's social graph). Multilingual coverage is a nice-to-have, not a default requirement.

## Decision

**Default:** `sentence-transformers/all-MiniLM-L6-v2`.
- 384 dimensions.
- `embedder_rev = "sbert-minilm-l6-v2-1"` recorded on every neuron.
- Loaded once at engine startup, cached in memory.
- CPU inference via sentence-transformers; no GPU required.

**Configurable.** Operators can switch embedders via `config/default.toml`:

```toml
[embeddings]
model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
dimensions = 384
revision = "sbert-multilingual-minilm-1"
```

Switching embedder after deployment means existing neurons are tied to the old embedder revision. They remain queryable as long as the engine keeps the old embedder loaded. See "Rotation" below.

**Dimensions fixed at 384 for Phase 0–7.** If an operator picks an embedder with different dimensions, they also drop and recreate the `neurons_vec` virtual table, which is a manual migration documented in `docs/runbooks/embedder_dimension_change.md`.

## Consequences

**Easier:**
- Defaults work out of the box. `uv sync` pulls `sentence-transformers`; on first use, the model downloads (~80 MB) and caches to `~/.cache/huggingface`.
- CPU inference is fast enough. ~5 ms per embedding on the target VM.
- Deployment footprint small. Model file is < 100 MB; memory footprint at runtime < 200 MB.
- No external API keys required. First-time users don't hit friction.
- Ubiquity. Almost every AI tutorial uses this model, so errors have search-engine answers.

**Harder:**
- Quality is middle-of-the-pack. Operators with demanding retrieval needs will want to upgrade. Upgrade path is documented but involves re-embedding.
- Multilingual support is weak. Operators with non-English counterparties should switch; this is documented but the default won't serve them well.
- 384 dimensions is the project-wide standard for Phase 0–7. If an operator picks a 768-dim embedder, they run into the dimension-change runbook.

**Future constraints:**
- Re-embedding a large persona is expensive but tractable. 100k neurons at 5ms each is ~8 minutes of CPU time. We document a background re-embedder job in Phase 6 that handles this offline.
- The `embedder_rev` column on neurons is load-bearing. Filter vector queries to match the currently-loaded revision. If you load two embedders (old for legacy rows, new for fresh ones), the retrieval engine keeps them separate.

## Rotation procedure

When an operator wants to upgrade to a better embedder:

1. Install the new model (e.g., `bge-base-en-v1.5`).
2. Update `config/default.toml`:
    ```toml
    [embeddings]
    model = "BAAI/bge-base-en-v1.5"
    dimensions = 768                            # note: different from default
    revision = "bge-base-en-1.5-2026-04"
    ```
3. Run migration for dimension change: recreate `neurons_vec` with new dimension.
4. Run `memory-engine embed reindex --from sbert-minilm-l6-v2-1 --batch 500`. The reindex job:
   - Loads both old and new embedders.
   - For each neuron with `embedder_rev = 'sbert-minilm-l6-v2-1'`, re-embeds its content with the new model.
   - Writes the new embedding to `neurons_vec`.
   - Updates the neuron's `embedder_rev` to the new value.
5. Verify. Run a test suite of queries against the reindexed persona; compare MRR@10 with the pre-reindex baseline.
6. Log a drift entry in `docs/blueprint/DRIFT.md` noting the embedder migration and measured impact.

Reindex is additive; old embeddings are superseded by new, but the column is updated in place. This is an allowed mutation (the neuron row itself is not changed, only the `embedder_rev` and corresponding `neurons_vec` entry).

## Alternatives considered

- **`all-mpnet-base-v2`.** Higher quality than MiniLM, but 5x the size and 3x the latency. Worth the cost for quality-demanding operators; not for the default.
- **`bge-small-en-v1.5`.** Strong MTEB performance. Considered as the default. Rejected because ubiquity of MiniLM wins slightly for a reference implementation; operators upgrading will naturally land on bge as a later choice.
- **OpenAI `text-embedding-3-small`.** Excellent quality and fast. Rejected as default because it requires API keys and network dependency in the hot path. Operators with OpenAI infrastructure can configure it.
- **Voyage `voyage-3-lite`.** Strong quality-per-cost. Same concern as OpenAI: adds external dependency.
- **Custom embedder trained on conversational data.** Rejected for the blueprint; a later project could fine-tune an embedder on memory_engine-specific traces, but not in the first release.

## Revisit if

- `all-MiniLM-L6-v2` becomes unmaintained or stops loading in recent sentence-transformers versions.
- A new embedder emerges with the same size + speed profile but substantially better retrieval quality (>10% MRR@10 improvement on our eval baseline).
- Multilingual coverage becomes a first-class requirement for the default deployment.
- Embedding latency becomes a measurable bottleneck (not likely at < 10 QPS per persona).
