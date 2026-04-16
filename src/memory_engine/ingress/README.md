# `memory_engine.ingress`

The ingest pipeline. Incoming events from any adapter pass through here before reaching the event log.

## What belongs here

- `pipeline.py` — main ingest flow: verify signature → classify scope → compute content hash → check idempotency → append event.
- `classify.py` — scope classification (calls policy plane with `classify_scope` site).

## What does NOT belong here

- Channel-specific normalization (→ `memory_engine.adapters.<channel>`). Ingress takes already-normalized events.
- Signature generation — MCPs sign; ingress only verifies.
- Consolidation — that's an async downstream step in `core.consolidator`.

## R1, R2 — signatures and scope are mandatory

Every event entering the log has:
- A verified Ed25519 signature against the registered MCP public key (R1).
- A scope classification — defaults to `private` on classifier failure (R2).

There is no code path that appends an event without these. If you find one, it's a governance violation.

## Conventions

- Ingress is async but not background. The caller (adapter webhook) awaits the event before responding. Keeps idempotency tight.
- Verification happens before *any* write. A bad signature must not produce any row, not even a quarantine entry.
- Idempotency keys are caller-namespaced to avoid cross-source collisions (see Phase 0 common pitfalls).
