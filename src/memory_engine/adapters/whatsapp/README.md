# `memory_engine.adapters.whatsapp`

WhatsApp Business API adapter. See `docs/blueprint/06_whatsapp_adapter.md` for the full spec and `docs/phases/PHASE_5.md` for execution.

## Setup

See `docs/runbooks/whatsapp_setup.md` for Meta Business API provisioning — app creation, phone number verification, webhook configuration, access token generation.

## T3 and T11 — release gates

Phase 5 does not ship until:
- `tests/invariants/test_phase5_T3.py` passes 100% (cross-counterparty isolation).
- `tests/invariants/test_phase5_T11.py` passes 100% (prompt injection resistance on 50+ adversarial inputs).

These are not aspirational. If they fail, the adapter is unsafe to deploy.

## Groups are counterparties

A WhatsApp group has one `counterparties` row. Individual senders within the group are NOT separate counterparties — their identities are stored in `events.sender_hint` for audit only, never used in retrieval.

## Media handling

Default: media is referenced but not downloaded. Enable per-MCP via config. Size cap: 5 MB. Allowed types in Phase 5: text and image. Audio transcription and image OCR are Phase 7+.
