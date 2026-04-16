# `memory_engine.adapters`

Channel-specific adapters. Phase 5 ships WhatsApp; future channels (Slack, email, SMS) go in sibling subdirectories.

## What belongs here

One subpackage per channel:
- `whatsapp/` — Phase 5.
- Future: `slack/`, `email/`, etc.

Each channel subpackage contains:
- `webhook.py` — FastAPI route for incoming messages.
- `ingress.py` — payload normalization into our event shape.
- `outbound.py` — sending approved messages out.
- `mcp.py` — MCP lifecycle specific to this channel.
- `client.py` — HTTP client for the channel's API.

## What does NOT belong here

- Generic ingress logic (→ `memory_engine.ingress`).
- Generic outbound approval (→ `memory_engine.outbound`).
- Signature verification primitives (→ `memory_engine.policy.signing`).

## Conventions

- Each adapter owns exactly one MCP per persona in Phase 5. Multi-MCP per channel per persona is post-Phase-7.
- Adapters NEVER write to core tables directly. They call `memory_engine.ingress.pipeline` for inbound and `memory_engine.outbound.approval` for outbound.
- Webhook handlers return < 500ms. Media downloads, tone profile refreshes, anything slow — queue async; don't block the handler.
