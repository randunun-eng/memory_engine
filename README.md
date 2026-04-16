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

Phase 0 (skeleton). Not ready for use. See `CLAUDE.md` §8 for current focus and `docs/phases/PHASE_0.md` for what to build first.

## License

Apache-2.0. See [`LICENSE`](./LICENSE).
