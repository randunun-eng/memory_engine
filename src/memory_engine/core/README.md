# `memory_engine.core`

Core domain logic. The event log, neurons, working memory, consolidation, grounding gate, contradiction detection, reinforcement, decay, pruning.

## What belongs here

- Data access for the core tables: `events`, `neurons`, `working_memory`, `quarantine_neurons`, `episodes`.
- Pure domain logic that operates on those entities.
- The consolidator loop's phases: promote → extract → contradiction → reinforce → decay → prune.
- The grounding gate — the load-bearing check between LLM extraction and cortex.
- Content hashing and canonical serialization.

## What does NOT belong here

- HTTP routes (→ `memory_engine.http`).
- DB connection management or migration running (→ `memory_engine.db`).
- LLM calls or prompt rendering (→ `memory_engine.policy`).
- Retrieval queries (→ `memory_engine.retrieval`).
- Signing, verification, vault (→ `memory_engine.policy`, `memory_engine.core.vault`).
- Outbound approval logic (→ `memory_engine.outbound`).
- Identity documents (→ `memory_engine.identity`).

## Key files

| File | Phase | Purpose |
|---|---|---|
| `events.py` | Phase 0 | `append_event`, `get_event`, `compute_content_hash` |
| `working.py` | Phase 2 | Working-memory ring buffer |
| `consolidator.py` | Phase 2 | Main consolidation loop |
| `grounding.py` | Phase 2 | Grounding gate — citations resolve + similarity + LLM judge |
| `contradiction.py` | Phase 2 | Same-entity-pair detection and supersession |
| `extraction.py` | Phase 2 | LLM-driven extraction (dispatches to policy plane) |
| `reinforce.py` | Phase 2 | LTP reinforcement from retrieval traces |
| `decay.py` | Phase 2 | LTD with per-tier half-lives |
| `prune.py` | Phase 2 | Low-activation pruning |
| `synapses.py` | Phase 3 | Edge creation between neurons |
| `cooccurrence.py` | Phase 3 | Co-occurrence analysis |
| `counterparties.py` | Phase 4 | Counterparty CRUD |
| `tone.py` | Phase 4 | Tone profile analysis |
| `vault.py` | Phase 2 | Secret vault (R7) |

## Conventions

- Every function in this module is `async def` — I/O-bound through aiosqlite.
- Reinforcement always distinguishes `source_count` (every hit) from `distinct_source_count` (unique source events). Rule 15 is load-bearing; the mem0 echo bug came from collapsing these.
- Content hashing is canonical (sorted keys, compact JSON, UTF-8). Same payload produces identical hash on every call.
- Functions that write take an explicit `conn` argument; never open their own connection. The single-writer discipline (rule 9) depends on this.
