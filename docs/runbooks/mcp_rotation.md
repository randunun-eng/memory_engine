# Runbook: MCP key rotation

> Rotate an MCP's Ed25519 signing keypair. Historical events signed with the old key remain verifiable; new events use the new key.

## When to rotate

- Suspected compromise of the MCP private key.
- Scheduled rotation (e.g., yearly policy).
- Change of ownership of the MCP process.

Do NOT rotate unnecessarily. Rotation is safe but introduces a brief window where both keys are active.

## Prerequisites

- Operator access to the engine host (CLI).
- Access to the MCP process (where the current private key lives).
- Downtime window is not required; rotation is online.

## Procedure

### 1. Generate a new key pair on the MCP side

The MCP process (the thing that holds the private key and signs outgoing events) generates a fresh Ed25519 keypair. How this is done depends on how the MCP was built. For the reference implementation:

```bash
# On the MCP host
python -c "
from nacl.signing import SigningKey
import base64
sk = SigningKey.generate()
print('private:', base64.b64encode(bytes(sk)).decode())
print('public:', base64.b64encode(bytes(sk.verify_key)).decode())
"
```

Save both. The MCP will swap to the new private key in a later step.

### 2. Register the new key on the engine

```bash
uv run memory-engine mcp register-rotation <persona_slug> \
  --current-name primary \
  --new-public-key <NEW_PUBLIC_KEY_B64> \
  --new-name "primary-2026-04" \
  --effective-from "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

What this does:
- Inserts a new row in `mcp_sources` with the new public key.
- Does NOT revoke the old key yet.
- Returns when the new key is live — typically < 1 second.

Now the engine accepts events signed with EITHER key. Both are active.

### 3. Switch the MCP to the new private key

Deploy the new private key to the MCP process. Method depends on your deployment:

- If the MCP reads the key from an env var, update the env var and restart.
- If from a file, write the new file and SIGHUP.
- If from an HSM, rotate inside the HSM.

After this step, the MCP signs new events with the new key. Old events already in the log keep their signatures; they were signed with the old key and the engine still has that key registered.

### 4. Verify new events are being signed with the new key

Send a test event from the MCP. On the engine:

```bash
uv run memory-engine mcp recent-events <persona_slug> --limit 5
```

Output should show the most recent events' `mcp_source_id` matches the new `mcp_sources` row. If they still reference the old row, the MCP hasn't switched yet.

### 5. Revoke the old key

Once you're confident the MCP is producing new-key-signed events:

```bash
uv run memory-engine mcp revoke <persona_slug> \
  --name primary \
  --reason "rotated to primary-2026-04"
```

The old row gets `revoked_at` set. Historical events are still verifiable (the verification code tries active keys first, then revoked keys with `revoked_at > event.recorded_at`).

### 6. Record the rotation

Append to `docs/runbooks/rotations.md`:

```
- 2026-04-16: rotated primary → primary-2026-04 (reason: scheduled annual rotation)
```

## Verification after rotation

```bash
# List all MCPs for the persona
uv run memory-engine mcp list <persona_slug>
# Expect:
#   id=1  name=primary              status=revoked   revoked_at=2026-04-16T...
#   id=2  name=primary-2026-04      status=active    registered_at=2026-04-16T...

# Verify a random historical event
uv run memory-engine mcp verify-event <event_id>
# Should report "signature valid against mcp_sources.id=1 (revoked 2026-04-16)".
```

## Troubleshooting

**New events rejected with SignatureInvalid after switch** — the MCP and engine disagree on the public key. Confirm via `mcp list` that the public key the engine expects matches what the MCP is using.

**Historical event verification fails after revocation** — the old key's `revoked_at` is earlier than the event's `recorded_at`. This means the event was actually signed after the key was "rotated" — suggests a clock issue on the MCP or a misordered rotation.

**Both keys accepted indefinitely** — you forgot step 5. Revoke the old key; reduces key-material surface area.

## Emergency rotation (compromise)

If you suspect the private key was exposed:

1. Immediately run `memory-engine mcp revoke <persona_slug> --name primary --reason "suspected compromise"`. This stops accepting new events signed with the old key.
2. `memory-engine halt force --reason "investigating MCP compromise"`. Blocks ingest entirely.
3. Investigate: review recent events for anomalies; compare against MCP-side logs.
4. Generate a new keypair (step 1 above) and register (step 2). 
5. Release halt when you're confident the new setup is clean: `memory-engine halt release --reason "rotated after compromise"`.

Document the incident in `docs/runbooks/incidents/YYYY-MM-DD-mcp-compromise.md`.
