# `memory_engine.identity`

Identity documents — the authoritative, signed declaration of who the persona is and what it will not do. Rule 11: identity documents are authoritative, never derived from extraction.

## What belongs here

- `document.py` — Pydantic model for the identity document YAML schema.
- `loader.py` — load from file or `personas.identity_doc` column, verify signature, cache in memory.
- `drift.py` — drift detection. When consolidation extracts a `self_fact` that contradicts the identity, flag it.
- `signing.py` — owner-key signing (separate from MCP signing; see ADR 0005).

## What does NOT belong here

- Non-negotiable evaluation at outbound time (→ `memory_engine.outbound.nonneg`). That module *reads* from identity but owns the evaluator.
- Counterparty management (→ `memory_engine.core.counterparties`).
- Tone profiles (→ `memory_engine.core.tone`). Tone adapts per-counterparty; identity is per-persona.

## Rule 11 — identity is authoritative

Identity documents are:

- **Signed** by the persona owner at issuance. Verification runs at load time and on every cache miss.
- **Loaded**, never generated. The LLM cannot propose or accept modifications.
- **Edited** by an operator out-of-band (YAML file + `memory-engine identity load`), never by the engine itself.
- **Versioned** via the `personas.version` column. Re-loading bumps the version; history is preserved via git on the source YAML.

If an extraction produces a `self_fact` that contradicts identity values, non-negotiables, or role, the drift detector flags it. Operator reviews. Accepting a drift entry means updating the identity document (out of band) — the extraction does not quietly override.

## What's in an identity document

```yaml
schema_version: "1.0"
persona_slug: "...
owner: "..."
issued_at: "..."
role:            # title, domain, responsibilities
values:          # list of principles
non_negotiables: # rules that hard-block outbound
tone_defaults:   # formality, length, emoji
boundaries:      # topics off-limits or deflected
signature: "ed25519:..."
```

Full schema is defined in `document.py`'s Pydantic models. Starter template at `tests/fixtures/identity_documents/minimal.yaml`.

## Conventions

- The in-memory cache invalidates on `memory-engine identity load`. Do not re-cache without re-verifying the signature.
- Drift flags are advisory by default. They become blocking only if the operator configures `identity.drift_behavior = "halt"` in config — which is not the default.
- Identity file size matters for outbound latency. Keep documents focused; non-negotiables in particular should be concise and evaluator-friendly.
