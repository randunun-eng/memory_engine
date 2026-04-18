# Blueprint Drift Log

Known deviations between the blueprint documents in `docs/blueprint/` and the implementation in `src/`. Each entry is raised here first, then either resolved (reconcile code to blueprint), accepted (update blueprint), or deferred (explicit decision to diverge, with reason).

Entries are append-only. Once resolved, the entry stays with its resolution noted.

| Date | Area | Divergence | Resolution | Status |
|---|---|---|---|---|
| 2026-04-16 | migration/phase0 | Removed `WHEN OLD.type != 'halted'` guard on `events_immutable_update` trigger; changed trigger mechanism from `RAISE(ABORT, ...)` to non-existent-table reference to produce `OperationalError` instead of `IntegrityError` | Accepted — guard was speculative (halt events are inserted, never updated) and weakened rule 1 by creating an exception with no consumer; error type change aligns with what invariant tests check, and `OperationalError` is semantically more correct than `IntegrityError` for an immutability enforcement | Resolved |
| 2026-04-16 | retrieval/phase1 | BM25 score filter changed from `> 0.0` to `!= 0.0` to handle rank-bm25 negative IDF in small corpora | Accepted — BM25Okapi produces negative IDF scores when corpus has < 3 documents, which made temporal as_of queries (where the matching corpus is often 1-2 documents) return empty; non-matching docs always score exactly 0.0 so `!= 0.0` correctly preserves relevance signal | Resolved |
| 2026-04-16 | retrieval/phase1 | BM25 index is rebuilt from scratch on every `recall()` call instead of being cached/incremental | Accepted as Phase 1 simplification — p99 is 19ms at 10k neurons so not a bottleneck yet; incremental maintenance deferred to Phase 6 when sustained QPS matters | Open |
| 2026-04-16 | retrieval/phase1 | `emit_trace_async` task retention set was local variable (GC could collect tasks mid-execution); fixed to module-level `_background_tasks` set; also changed from `async def` to `def` since it was never awaited | Bug fix — the original pattern created `_background_tasks: set` inside the function body, so it went out of scope on return and the task strong reference was lost; under load traces would silently vanish | Resolved |
| 2026-04-16 | retrieval/phase1 | Production-realistic p99=131ms (6x headroom to 800ms budget) — two caveats: (1) Phase 2 consolidator shares CPU with query embedder; concurrent embedding will spike latency unless run_in_executor separates them, (2) grounding gate triggers internal recall calls not present in Phase 1 load; re-measure at Phase 2 close with consolidator running | Documented as baseline caveat — not a divergence, just honest context for the 6x headroom claim | Open |
| 2026-04-16 | retrieval/phase1 | BM25 and query embedding could run concurrently (no data dependency until fusion step) but currently run sequentially; ~10ms async overhead in the pipeline | Phase 6+ optimization opportunity — don't act now, but the lever is asyncio.gather(bm25_search, embed_query) joining at RRF | Open |
| 2026-04-16 | grounding/phase2 | Grounding gate accuracy: 72% on 50 hand-labeled fixtures (TP=23, TN=13, FP=7, FN=7), threshold=0.40, embed=bag-of-words (not MiniLM). FP cluster: specificity inflation where word overlap is high but meaning was changed (u01, u06, u07, u08, u13, u15, u19). FN cluster: valid paraphrases with low word overlap. Real embeddings should improve both. | Baseline measurement — re-measure at Phase 7 with MiniLM and real LLM judge enabled | Open |
| 2026-04-16 | consolidator/phase2 | Contradiction check runs the LLM judge twice for the same overlapping neuron pair (once pre-insert for detection, once post-insert for supersession) — doubles LLM cost for contradiction cases | Phase 6 optimization: cache first check result or restructure to single-pass | Open |
| 2026-04-16 | consolidator/phase2 | LLM model pinned to `ollama/llama3.1:8b` in PolicyDispatch default. Changing the model requires re-measuring grounding accuracy because different models have different extraction and grounding characteristics. The 72% baseline was measured without an LLM judge (similarity-only). | Pin documented in config.py — changing model is a re-measurement event | Open |
| 2026-04-16 | healing/phase3 | Invariant scan duration: 3ms at 50 events + 10 neurons. Pessimistic linear extrapolation to 10k events: 0.6s. Sub-linear (indexed queries dominate): ~0.2s. Budget is 30s; 50x headroom at 10k. Re-measure at Phase 6 with `--perf` flag against real 10k-event database. | Baseline measurement — headroom is large, no action needed until Phase 6 | Open |
| 2026-04-16 | observability/phase6 | Metrics registry is built and tested (7 tests passing) but NOT wired into call sites. No module outside `observability/` imports `metrics.counter/gauge/histogram`. Rendering `/metrics` today would return an empty registry. | Phase 7 pre-week task: instrument ~50-100 call sites across consolidator, retrieval, policy/dispatch, healing/checker, outbound/approval, adapters/whatsapp/mcp, ingress. 1-2 days of work. Without this, Phase 7 observability dashboards show no data. | Open |
| 2026-04-16 | backup/phase6 | First DR drill completed in 0s elapsed on same host with synthetic fixture (`drills/2026-04-16.md`). Proves round-trip works; does NOT prove real-host restore. Same filesystem, same process space, tiny data. | Phase 7 pre-week task: run drill on fresh Docker container or VM with a realistic-sized DB. Record real RTO in `drills/`. Required before first real counterparty message. | Open |
| 2026-04-17 | http/phase6.5 | `create_persona-inline-SQL`: POST /v1/personas uses inline `INSERT INTO personas (slug) VALUES (?)` in the route handler. No dedicated `create_persona()` helper exists; Phase 0/1 tests used raw SQL. `owner_public_key` is accepted in the request body but not persisted (the `personas` schema has no such column). | Replace with proper `create_persona()` helper + `owner_public_key` column migration when identity-document signature verification lands (Phase 7+). | Open |
| 2026-04-17 | http/phase6.5 | `ingest-3-lookups-inline-SQL`: POST /v1/ingest performs three lookups inline in the route — (slug → persona_id), (persona_id + external_ref → counterparty_id, create if missing), (persona_id → most-recent active mcp_source for public_key + mcp_source_id) — before calling `append_event()`. Logic duplicates the resolution pattern in `ingest_whatsapp_message()`. | Extract to a shared `_resolve_ingest_context()` helper when a third call site needs the same resolution. Two call sites (this route + WhatsApp adapter) is still below the rule-of-three threshold. | Open |
| 2026-04-17 | http/phase6.5 | `identity-load-signature-not-verified`: POST /v1/identity/load calls `save_identity()` which parses and persists the YAML but does NOT verify the signature carried in the document. `bootstrap.sh` signs the YAML with `PERSONA_OWNER_PRIVATE_KEY`; no code path today checks that signature. | Defer signature verification to Phase 7+ when the threat model includes untrusted identity updates. Requires `owner_public_key` to be persisted on the persona row first (see `create_persona-inline-SQL`). | Open |
| 2026-04-17 | identity/schema | `identity-schema-mismatch-twincore-vs-phase4`: The twincore-alpha starter `personas/<slug>.yaml` schema (`persona_slug`, `schema_version`, `issued_at`, `owner`, rich `role`/`values`/`tone_defaults`, structured `non_negotiables: [{id, rule, evaluator, trigger_patterns}]`) does NOT match Phase 4's `parse_identity_yaml` schema (`persona`, `version`, `signed_by`, `signed_at`, bare-string `non_negotiables: [str]`). Discovered at integration time because neither side tested against the other's consumer. POST /v1/identity/load returns 400 "missing required field 'persona'" on the alpha YAML; bootstrap.sh step 7 skips the call and only signs the YAML on disk. Twin-agent reads the YAML directly from disk so its persona prompts still work. Memory_engine.personas.identity_doc stays NULL — Phase 4 drift flags + outbound identity checks are effectively unused in alpha. | **Phase 7 backlog item**: define canonical identity schema v2, migrate both `parse_identity_yaml` and the twincore-alpha starter YAML to it, delete this DRIFT entry. Add a smoke test that loads the alpha-shipped starter YAML through the parser to catch future schema drift before integration. | Open |

## Conventions

- Raise a drift entry the same commit the divergence is introduced.
- Format: one-liner in the table above, full explanation in a subsection below.
- When resolving, update the Status column but do not edit the original entry text.

## Open entries

### 2026-04-16: BM25 index rebuild-per-call (retrieval bm25.py)

`BM25Index.build()` is called fresh on every `recall()` invocation — it loads all matching neurons from SQLite into memory and constructs a new `BM25Okapi` index. At 10k neurons this costs ~10ms (the dominant cost in a BM25-only query), which is fine for Phase 1's single-user, low-QPS scenario.

At sustained 100 QPS this becomes 100 rebuilds/s. At 100k+ neurons the rebuild time will be linear and start to matter. The cache isn't warm across queries — IDF tables and tokenized corpus are recomputed each time.

Deferred to Phase 6 (observability/operational hardening). The fix is a per-persona memoized BM25 index that invalidates on neuron insert/supersede/prune events.

### 2026-04-16: Grounding gate accuracy baseline (grounding.py)

**Measurement:** 72% accuracy on 50 hand-labeled fixtures using bag-of-words embeddings (not real MiniLM) and similarity-only gate (LLM judge disabled), threshold=0.40.

**Breakdown:** TP=23, TN=13, FP=7, FN=7. Precision=76.7%, Recall=76.7%.

**FP cluster (7 false positives — ungrounded candidates accepted):** All are specificity-inflation cases where the candidate shares most words with the source but adds fabricated details. Bag-of-words embedding doesn't distinguish "works at Google as engineer" from "works at Google as senior engineer earning $200k" because the added words don't dominate the similarity. Real MiniLM embeddings should partially address this, but the LLM judge is the proper fix — it reasons about whether new information was introduced.

**FN cluster (7 false negatives — grounded candidates rejected):** Valid paraphrases where word choice diverged enough from the source. E.g., "relocated" vs "moved to" — different stems, low bag-of-words overlap. Real embeddings handle this well.

**Threshold sensitivity:** 0.40 is the current default. Lowering to 0.30 would reduce FN but increase FP. With real embeddings, 0.40 should be a better balance. Re-measure at Phase 7 with MiniLM and LLM judge enabled.

**P=R symmetry caveat:** Precision and recall are identical (76.7%) because the fixture set is near-balanced (30 positive / 20 negative) and errors are symmetric (7 FP / 7 FN). In production, class imbalance will be heavy — most extractions are plausible, only a small fraction ungrounded — so FP rate and FN rate will diverge. Track them separately from Phase 7 onward; aggregate accuracy will mask real performance shifts.

**Re-measurement plan:** Before Phase 7 operator week, run the same 50 fixtures through the real pipeline (MiniLM embeddings + LLM judge on semantic/procedural tiers). Record the second data point here. The delta between bag-of-words baseline (72%) and real-pipeline number is the Phase 2→7 quality evidence.

### 2026-04-16: Double contradiction check (consolidator.py)

The consolidator runs `find_overlapping_neurons` + `check_contradiction` twice for the same neuron pair: once before inserting the new neuron (to detect contradictions) and once after (to execute supersession). The pre-insert check result is not cached or passed through, so the LLM judge is called twice for the same pair.

At Phase 2 volumes (mock LLM, single-user) this is imperceptible. Under real LLM load, contradiction cases will cost 2x. Fix is to cache the first check result keyed on (existing_id, candidate_content_hash) and reuse at supersession time. Deferred to Phase 6.

### 2026-04-16: LLM model pin (policy/dispatch.py, config.py)

Model pinned to `ollama/llama3.1:8b` as the default in PolicyDispatch. The 72% grounding accuracy baseline was measured without an LLM judge — changing the model (even within the Llama family) requires re-measuring because extraction quality and grounding judge behavior are model-dependent.

The pin lives in dispatch.py as a default parameter and in config.py as the broader embeddings.model setting. Changing either is a re-measurement event: run `tests/eval/test_grounding_accuracy.py` and update this DRIFT entry with the new numbers.

**Digest pinning note:** Ollama tag `:8b` is mutable — Meta can republish weights with different quantization under the same tag. For full reproducibility before Phase 7, pin to digest: `llama3.1:8b@sha256:...`. Ollama supports this. Not blocking for Phase 2-6 (local dev, mock LLM in tests), but required before the operator week where "measured against this exact model" must be verifiable.

### 2026-04-16: Invariant scan duration baseline (healing/checker.py)

**Measurement:** 3ms average on 50 events + 10 neurons (3 runs, consistent). Full scan of 21 invariants across 16 rules.

**Extrapolation to 10k events:**
- Pessimistic (linear with event count): 0.6s — assumes all checks scale linearly. Most do (they iterate neurons and query events per neuron).
- Optimistic (sub-linear, indexed): 0.2s — indexed queries (trigger existence, scope validation, count comparisons) are O(1) or O(log n). Only provenance checks (rule 2, 6, 14) iterate neurons and do per-citation lookups.

**Budget:** 30s. Current headroom is 50x even pessimistic. The bottleneck at 10k will be the per-neuron citation resolution checks (rules 2, 14) which do `SELECT 1 FROM events WHERE id = ?` for each cited event. These are indexed lookups but the loop count grows with neuron count. If this becomes a problem, batch into a single `WHERE id IN (...)` query.

**Re-measurement:** Phase 6 operational hardening adds the `--perf` flag test with a real 10k-event fixture. The number here is the Phase 3 baseline.

## Resolved entries

### 2026-04-16: Immutability trigger mechanism (migration 001)

The Phase 0 doc specified `RAISE(ABORT, 'events are immutable (rule 1)')` in the immutability triggers. Two issues:

1. **`RAISE(ABORT, ...)` in SQLite triggers produces `IntegrityError` in Python's sqlite3 module** (mapped from `SQLITE_CONSTRAINT_TRIGGER`), but the invariant tests (`tests/invariants/test_phase0.py`) assert `aiosqlite.OperationalError`. `IntegrityError` semantically means constraint violation (FK, CHECK, UNIQUE), which isn't quite what an immutability trigger is doing. The fix uses a reference to a non-existent table (`SELECT * FROM "events are immutable (rule 1)"`) which naturally produces `OperationalError` with the rule text in the message.

2. **The `WHEN OLD.type != 'halted'` guard on the update trigger was speculative.** It anticipated Phase 3's halt mechanism needing to update events, but halt events are events — they're inserted, never updated. The guard created an exception to rule 1 with no consumer, weakening the invariant. Removed.

Both changes accepted as improvements over the blueprint spec.

### 2026-04-16: BM25 negative IDF in small corpora (retrieval bm25.py)

`rank_bm25.BM25Okapi` computes IDF as `log((N - df + 0.5) / (df + 0.5))`. When the corpus has fewer than ~3 documents, every matching term has `df ≈ N`, making the IDF expression `log(<1)` = negative. This means a document with perfect token overlap scores negative, and the original `scores[i] > 0.0` filter discarded it as irrelevant.

This surfaces in `as_of` temporal queries where the lens + temporal filter narrows the corpus to 1-2 documents (e.g., querying a superseded neuron at a point in time where it was the only match). Non-matching documents always score exactly `0.0`, so changing the filter to `!= 0.0` correctly preserves the relevance signal without introducing false positives.

The blueprint spec doesn't prescribe BM25 implementation details, so this is an implementation decision rather than a blueprint divergence. Accepted.

### 2026-04-16: Fire-and-forget task retention bug (retrieval trace.py)

`emit_trace_async()` used `asyncio.create_task()` with the task-retention `_background_tasks` set, but the set was declared as a **local variable** inside the function body. When the function returned, the set went out of scope. Python's GC could then collect the set and with it the only strong reference to the task, causing the task to be silently cancelled mid-execution.

Under low load (tests, single queries) this would rarely manifest because the task typically completes before GC runs. Under sustained load (75+ QPS), the probability of a task being collected mid-write increases, causing retrieval traces to silently vanish. There's no error, no log — the task just disappears.

Fix: hoist `_background_tasks` to module level. Also changed `emit_trace_async` from `async def` to `def` since it was never awaited — the function schedules work and returns synchronously, which is clearer in the type signature.
