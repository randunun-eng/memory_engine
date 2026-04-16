# `memory_engine.outbound`

The outbound approval pipeline. Every message leaving the twin flows through here. There is no bypass; adapters (Phase 5) call `approve_outbound()` and deliver only what it returns.

## What belongs here

- `approval.py` — `approve_outbound()`. Orchestrates the pipeline: redactor → non-negotiables → identity alignment → approved draft.
- `redactor.py` — pattern and cross-counterparty redaction.
- `patterns.py` — redaction pattern definitions, externalized so operators can extend without editing code.
- `nonneg.py` — non-negotiable evaluator (pattern or LLM-backed).
- `generator.py` — reply draft generation. Dispatches to `policy.dispatch` with `"generate_reply"` site.

## What does NOT belong here

- Actual send-to-WhatsApp (→ `memory_engine.adapters.whatsapp.outbound`).
- Identity document loading (→ `memory_engine.identity`).
- Retrieval (→ `memory_engine.retrieval`). Outbound draft generation *uses* retrieval but doesn't own it.

## Pipeline order is not negotiable

```
Draft → Redactor → Non-negotiables → Identity alignment → Approved
```

Privacy redaction happens first. This ensures that even if the non-negotiable evaluator hallucinates an exception ("this PII is fine because the user asked"), the redactor has already stripped it. Defence in depth.

## Rule 13 — pillar hierarchy

`privacy > counterparty > persona > factual`. When stages conflict:

- Privacy concern blocks. Always.
- Counterparty-specific rules beat persona-level rules.
- Persona role/values beat bare factual accuracy.
- Factual accuracy beats stylistic preference.

Encoded in the pipeline order (privacy redactor first) and in the non-negotiable evaluator's prompt (explicitly instructs the judge to prefer refusal over clever accommodation).

## Conventions

- Every approval decision writes exactly one event: `outbound_approved` or `outbound_blocked` with the stage and reason. Auditable trail of every send and every refusal.
- Blocked drafts are never delivered. The caller sees `ApprovalResult.blocked(...)` and acts accordingly — usually by logging and leaving the counterparty without a reply.
- The redactor's `allowed` set includes the active counterparty's email/phone/name so they can be referenced back to the counterparty themselves. Emails belonging to anyone else get redacted.
