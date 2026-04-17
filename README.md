# memory_engine

Reference implementation of Wiki v3: a neural-inspired memory orchestration system for digital twins and digital employees. Event-sourced, bi-temporal, grounded against source citations. Hard privacy invariants, per-persona MCP adapters. Targets 80% reliable orchestration — honestly, not perfection.

## Start here

**Contributors and agents read [`CLAUDE.md`](./CLAUDE.md) first.** It is the authoritative instruction file for this repository — philosophy, governance rules, phase plan, acceptance criteria.

## Documentation map

| Where | What |
|---|---|
| [`CLAUDE.md`](./CLAUDE.md) | Authoritative instruction file. Read first. |
| [`docs/phases/`](./docs/phases/) | Per-phase execution guides. [Phase 0](./docs/phases/PHASE_0.md) is where coding starts. |
| [`docs/blueprint/`](./docs/blueprint/) | Eight blueprint documents — the full architectural history. |
| [`docs/SCHEMA.md`](./docs/SCHEMA.md) | Consolidated database schema across all migrations. |
| [`docs/CODING.md`](./docs/CODING.md) | Coding standards and conventions. |
| [`docs/TESTING.md`](./docs/TESTING.md) | Test strategy — invariants, integration, eval. |
| [`docs/SECURITY.md`](./docs/SECURITY.md) | Twelve security requirements and the threat model. |
| [`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md) | PR conventions and review expectations. |
| [`docs/GLOSSARY.md`](./docs/GLOSSARY.md) | Domain terminology reference. |
| [`docs/adr/`](./docs/adr/) | Architecture decision records. Why we chose what we chose. |
| [`docs/runbooks/`](./docs/runbooks/) | Operational procedures. |
| [`docs/diagrams/`](./docs/diagrams/) | Mermaid architecture diagrams. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Version history (populated as phases close). |

## Blueprint

Eight design documents in [`docs/blueprint/`](./docs/blueprint/) capture the full architectural history: base design, contradiction fixes, evaluation framework, identity layer, anti-hallucination hardening, WhatsApp adapter spec, synthesis with gap analysis, and closure specifications for the three blocking operational gaps.

## Status

**Phase 6 complete (v0.7.0).** Six of seven phases shipped. Not yet production-ready — Phase 7 (First Internal User) requires three weeks of real operator usage to capture an eval baseline and validate end-to-end behavior under live traffic.

| Phase | Status | What shipped | Tag |
|---|---|---|---|
| 0 — Skeleton | ✅ | Event log, signatures, migrations runner | `v0.1.0` |
| 1 — Retrieval | ✅ | BM25 + vector + graph + RRF, lens enforcement | `v0.2.0` |
| 2 — Consolidator + Grounding | ✅ | Policy plane, grounding gate, LLM-driven extraction | `v0.3.0` |
| 3 — Invariants + Healer | ✅ | 21 checks across 16 rules, durable halt, healer loop | `v0.4.0` |
| 4 — Identity + Counterparties | ✅ | Identity docs, non-negotiables, 5-step approval, PII redaction | `v0.5.0` |
| 5 — WhatsApp Adapter | ✅ | Per-persona MCP, T3 + T11 release gates both green | `v0.6.0` |
| 6 — Blocking Gaps | ✅ | Observability, backup/DR, prompt shadow harness, first DR drill | `v0.7.0` |
| 7 — First Internal User | ⏳ | Three weeks live, eval baseline, first real operator | — |

**Current metrics**: 175 tests pass (121 integration + 54 invariant), ruff + mypy clean, T3 and T11 release gates 100% green.

See `CLAUDE.md` §8 for current focus, `CHANGELOG.md` for phase-by-phase release notes, and `docs/blueprint/DRIFT.md` for documented deviations between blueprint and implementation.

## Highlights

- **Event-sourced, not CRUD.** Every write is an immutable event; all derived state (neurons, synapses, episodes, embeddings) is rebuildable from the log. Rule 1 is enforced by SQLite triggers, not Python discipline.
- **Grounding gate before promotion.** Every candidate neuron cites at least one source event, citations must resolve, and candidate content must share meaningful overlap with cited events. Rule 14 at the schema level (`CHECK (json_array_length(source_event_ids) >= 1)`).
- **Distinct sources count; repetition doesn't inflate.** The [mem0 audit](https://github.com/mem0ai/mem0/issues/4573) showed 808 echo copies of one hallucination. We track `distinct_source_count` separately from `source_count` and rank by distinct. Rule 15.
- **Privacy invariants are hard, not soft.** Cross-counterparty leaks, PII egress, and scope violations halt the system. Halt state is durable (survives restart). 100% T3 (cross-counterparty isolation) and T11 (prompt injection resistance) release gates.
- **One LLM entry point.** All LLM calls flow through `policy/dispatch.py`. CI greps for `from openai`, `from anthropic`, `import litellm` outside the policy plane — violations fail the build.
- **SQLite by default.** `uv sync && uv run memory-engine serve` starts a single-process memory engine. Postgres + pgvector path tested in CI for scale-up.
- **Honest about its limits.** Targets 80% reliable orchestration. The [HaluMem benchmark](https://arxiv.org/abs/2511.XXXXX) showed no memory system exceeds 62% extraction accuracy or 70% QA under long-context conditions. Hard invariants are 100%; everything else is probabilistic.

## License

Apache-2.0. See [`LICENSE`](./LICENSE).
