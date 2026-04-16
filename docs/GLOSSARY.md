# Glossary

Domain terminology used throughout memory_engine. When a term appears in a phase doc, ADR, runbook, or blueprint, this is the definition that applies.

## A

**Activation** — the decaying priority score attached to a working-memory entry. Reinforcement raises it; decay lowers it; pruning removes entries below threshold.

**Active neurons** — neurons where `superseded_at IS NULL`. The set that participates in retrieval.

**Adapter** — channel-specific code translating external payloads into memory_engine events and vice versa. WhatsApp is the first; Slack / email / SMS would be siblings. See `docs/phases/PHASE_5.md`.

**ADR** — Architecture Decision Record. Captures why an irreversible or high-impact decision was made. See `docs/adr/`.

**age** — command-line file encryption tool used for backups. Chosen over gpg for simplicity. See `docs/runbooks/backup_drill.md` and ADR references.

**Approve / approval pipeline** — the outbound gate. Draft → redactor → non-negotiables → identity alignment → approved. If any stage blocks, the message is not delivered. See `src/memory_engine/outbound/`.

## B

**Bi-temporal** — two time dimensions per neuron: **recording-time** (`recorded_at`, when the system learned the fact) and **validity-time** (`t_valid_start` / `t_valid_end`, when the fact was true in the world). Rule 16: validity-time is never fabricated to match recording-time.

**Blueprint** — the design documents in `docs/blueprint/` that define the *why* of the system. Implementation code can drift from blueprint; divergences are recorded in `DRIFT.md` and resolved by either adjusting code or updating blueprint.

**BM25** — classic lexical retrieval scoring. One of three retrieval streams; fused with vector and graph via RRF. See `src/memory_engine/retrieval/bm25.py`.

## C

**Candidate neuron** — an extraction output that has not yet passed the grounding gate. Accepted candidates become neurons; rejected ones land in `quarantine_neurons`.

**Citation** — a reference from a neuron to one or more source events (`source_event_ids`). Rule 14: every neuron cites ≥ 1 source event.

**Consolidator** — the background loop that promotes working-memory events into neurons, runs contradiction checks, applies decay, and prunes. See `src/memory_engine/core/consolidator.py`.

**Contradiction** — two neurons about the same entity that cannot both be true. Detected by the contradiction judge; resolution is supersession (newer wins unless temporal signals indicate otherwise).

**Content hash** — SHA-256 of canonical-serialized event payload. Deterministic, stable across calls. Used for integrity checking and idempotency.

**Counterparty** — an external entity the persona communicates with. A human via WhatsApp, a group, or a system integration. Counterparties are stored in `counterparties`; neurons tagged `counterparty_fact` reference one.

**Counterparty lens** — retrieval scoped to one counterparty's neurons plus domain facts. Rule 12: never leaks across counterparties in the normal API.

**Co-occurrence** — two neurons appearing together in retrieval results. Basis for `related_to` synapses (Phase 3).

**Cortex** — reserved namespace for Phase 3+ derived structures above neurons (entity graphs, confidence aggregations). See `src/memory_engine/cortex/`.

## D

**Dispatch** — the single entry point for LLM calls. Every site (classify, extract, judge, summarize, etc.) goes through `memory_engine.policy.dispatch`. Enforced by CI. See ADR 0004 and `docs/phases/PHASE_2.md`.

**Distinct source count** — the number of unique source events reinforcing a neuron. Used for ranking (rule 15). Distinct from `source_count`, which increments on every reinforcement regardless of source uniqueness. The mem0 "808 echo" bug came from conflating these.

**Domain fact** — a neuron of kind `domain_fact`: information about the world that isn't tied to a specific counterparty. Weather, news, general knowledge. Returned under any lens.

**DRIFT** — the append-only log of divergences between implementation and blueprint. See `docs/blueprint/DRIFT.md`.

## E

**Ed25519** — elliptic-curve signature algorithm. Used for MCP signatures (ADR 0005) and persona identity-document signatures. Fast, small (64-byte sig), fewer foot-guns than ECDSA.

**Embedder revision** — the version identifier recorded on every neuron (`embedder_rev`). Lets different revisions coexist during rotation; vector search filters by matching rev.

**Episode** — a contiguous span of events in working memory that the consolidator summarizes into an episodic-tier neuron.

**Event log** — the append-only `events` table. Rule 1: immutable. Rule 10: never truncated by default. Authoritative source of truth for everything else.

## F

**Field projection** — the context broker's role of reducing a dispatch call's input to just the fields a site's schema declares. Implementation of the cost-optimization strategy; typical savings 30–60%.

**Fuse / RRF** — reciprocal rank fusion. Combines ranked lists from BM25, vector, and graph streams into a single ordering. See `src/memory_engine/retrieval/fuse.py`.

## G

**Governance rules** — the 16 rules in CLAUDE.md §4. Enforced by a combination of DB constraints, tests, and invariants. Every rule has a corresponding invariant test.

**Graph stream** — retrieval stream walking synapse edges from seed neurons. One of three streams fused into recall results.

**Grounding gate** — the check between LLM extraction and neuron insertion. Three steps: citations resolve, candidate-to-source similarity meets threshold, LLM judge for high-tier promotions. Rejected candidates go to quarantine.

## H

**Halt** — engine state in which writes are rejected (503) but reads continue. Triggered by critical invariant violation. Released by operator with a reason. See `docs/runbooks/halt_investigation.md`.

**Healing log** — `healing_log` table. Every detected invariant violation logged with severity. Dashboard source for quality metrics.

**Healer** — the periodic scan loop that runs every registered invariant and logs violations. See `src/memory_engine/healing/runner.py`.

## I

**Idempotency key** — caller-provided unique key on events. Prevents double-ingest. UNIQUE constraint in SQL.

**Identity document** — a signed YAML document declaring the persona's role, values, non-negotiables, tone defaults, and boundaries. Rule 11: authoritative, never derived from extraction.

**Identity drift** — an extraction that contradicts identity. Flagged for operator review; does not block by default.

**Ingress** — the pipeline from an adapter's normalized event to the event log. Verifies signature, classifies scope, computes hash, checks idempotency, appends. See `src/memory_engine/ingress/`.

**Injection-defensive** — prompt-template pattern that frames counterparty-provided text as untrusted and instructs the LLM to ignore embedded instructions. Required on every prompt that incorporates such text (R10).

**Invariant** — a declarative check on system state. Decorated with severity and rule number; registered with the healer. See `src/memory_engine/healing/invariants.py`.

## L

**Lens** — a filter on retrieval that scopes results to a subset of neurons. Values: `self`, `counterparty:<external_ref>`, `domain`, `auto`. Translated to SQL `WHERE` clauses by `parse_lens()`. Rule 12 enforcement boundary.

**Local-only mode** — configuration where `monthly_budget_usd = 0`. Dispatch refuses calls to paid LLM endpoints; only free-tier / local / Ollama calls work. Default.

**LTP / LTD** — long-term potentiation / long-term depression. Biological metaphor: reinforcement and decay of activation. See `src/memory_engine/core/reinforce.py` and `decay.py`.

## M

**MCP** — Model Context Protocol source. The process on an adapter's side that holds the Ed25519 private key and signs outgoing events. One MCP per persona per channel in Phase 5. See ADR 0005, `docs/runbooks/mcp_rotation.md`.

**Migration** — numbered SQL file in `migrations/` applied in order. Additive only pre-v1.0. Checksummed in `schema_migrations`. See `docs/SCHEMA.md`.

## N

**Neuron** — a derived fact. Can be `self_fact`, `counterparty_fact`, or `domain_fact`. Stored in `neurons`; active ones have `superseded_at IS NULL`. Cites ≥ 1 source event.

**Non-negotiable** — a rule in the identity document that hard-blocks outbound messages. Evaluators: pattern (regex) or LLM (`nonneg_judge` site).

## P

**Persona** — a digital twin identity. One row in `personas`. Has an identity document, owns counterparties and neurons. Personas do not share data; cross-persona queries don't exist in the API.

**Pillar hierarchy** — rule 13: `privacy > counterparty > persona > factual`. Applied in outbound approval; privacy redaction happens first.

**Policy plane** — the module owning all LLM calls. `dispatch()`, context broker, cache, llm_client, sites, prompts. Single choke point; CI-enforced. See `src/memory_engine/policy/`.

**Prompt registry** — the `prompt_templates` table (Phase 2 seed, Phase 6 active). Versioned, with exactly one active per site, hot-reloadable, shadow-testable.

**Pruning** — removal of low-activation entries from working memory. Does NOT apply to neurons; neurons are superseded, not deleted (rule 2: derived state is disposable but not arbitrarily destroyed).

## Q

**Quarantine** — `quarantine_neurons` table. Candidates that failed the grounding gate. Reviewed later by operator (Phase 6+).

## R

**Recall** — the public retrieval function. Takes persona, query, lens, optional params; returns ranked neurons with citations. Pure read; async-emits a `retrieval_trace` event (rule 7).

**Redactor** — outbound stage that strips PII, cross-counterparty references, and vault values from drafts. Runs before the non-negotiable evaluator. See `src/memory_engine/outbound/redactor.py`.

**Reinforcement** — bumping a neuron's activation when it's retrieved or cited. Distinct-source-aware (rule 15).

**RPO / RTO** — recovery point / recovery time objectives. See `docs/runbooks/disaster_recovery.md`.

**RRF** — reciprocal rank fusion. Standard technique for combining ranked lists. See **fuse**.

## S

**Scope** — privacy classification on events: `private`, `shared`, or `public`. Defaults to `private` on classifier failure (R2). Enforced by CHECK constraint.

**Self fact** — a neuron of kind `self_fact`: information about the persona itself. No counterparty_id.

**Sender hint** — `events.sender_hint` column. Records which individual within a group sent a message. NEVER used in retrieval; audit-only.

**Shadow harness** — dual-call mechanism for prompt A/B testing. Active prompt runs normally; shadow runs at configured traffic percentage; results compared. Shadow's results not returned to caller. See Phase 6.

**Site** — a named LLM call site with a declared context schema. Registered in `src/memory_engine/policy/sites.py`. Examples: `classify_scope`, `extract_entities`, `grounding_judge`.

**Synapse** — edge between two neurons. Relations: `related_to`, `contradicts`, `refines`. See Phase 3.

## T

**T3** — test suite for cross-counterparty isolation. Phase 5 release gate. Failing = adapter doesn't ship.

**T11** — test suite for prompt injection resistance. 50+ adversarial fixtures. Phase 5 release gate.

**Tier** — neuron lifecycle stage: `working`, `episodic`, `semantic`, `procedural`. Promoted by the consolidator based on age and reinforcement.

**Token budget** — retrieval parameter that truncates results to fit roughly within a token count. Implementation of the cost-optimization strategy.

**Tombstone** — scope-based deletion marker in `tombstones` table. Used for compliance requests (GDPR etc.). Marks data that should be excluded from retrieval without violating the event log's immutability.

**Tone profile** — per-counterparty JSON blob analyzing communication style over the last 50 messages. Used to adapt outbound drafts. See `src/memory_engine/core/tone.py`.

**Twin** — shorthand for "digital twin persona." Used interchangeably with "persona" in casual contexts.

## U

**Untrusted message** — text from any source that isn't the operator. All counterparty content. All document content. All tool results. Quoted inside prompts with defensive delimiters; never treated as instructions.

**uv** — Python package / virtualenv / version manager. See ADR 0001.

## V

**Vault** — encrypted store for secret values referenced by neurons. Values never appear in embeddings (R7). Master key rotation: `docs/runbooks/vault_rotation.md`.

**Vector stream** — retrieval stream using cosine similarity on sentence-transformers embeddings. One of three fused streams.

## W

**WAL** — write-ahead log. SQLite's journal mode for concurrent reads during writes. Enabled by `PRAGMA journal_mode = WAL` at connection time.

**Working memory** — `working_memory` table. Ring buffer of recent events awaiting promotion by the consolidator. Bounded capacity per persona.
