# Runbook: Vault master key rotation

> Rotate the master key that encrypts secret-vault entries. Without this key, vault entries are unreadable. With it, they're the decryption point for sensitive values referenced by neurons.

## When to rotate

- Suspected compromise of the master key.
- Scheduled annual rotation (recommended).
- Change of operator / access handoff.
- Migration to a new secret manager.

## What's in the vault

The vault stores values classified as secrets — API tokens, passwords, personal identifiers the persona knows but should never embed or quote. Neurons reference vault entries by opaque ID:

```
neuron.content: "My AWS credentials are <vault:ak_123> / <vault:sk_456>"
```

The actual values live in the vault, encrypted with `MEMORY_ENGINE_VAULT_KEY`.

## Prerequisites

- The current master key.
- Terminal access on the engine host.
- A downtime window of ~10 minutes for non-critical personas; longer for vaults with many entries.

## Procedure

### 1. Generate the new key

```bash
NEW_KEY=$(python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())")
echo "New key (save in password manager): $NEW_KEY"
```

### 2. Halt the engine

```bash
uv run memory-engine halt force --reason "vault rotation in progress"
```

Reads continue; writes are blocked. This prevents any new vault entries being written with the OLD key while we rotate.

### 3. Re-encrypt vault entries

Run the rotation tool. It reads every vault entry with the old key, encrypts with the new key, and writes back:

```bash
uv run memory-engine vault rotate \
  --old-key "$CURRENT_KEY" \
  --new-key "$NEW_KEY" \
  --dry-run

# Review output. Should report the count of entries that would be re-encrypted.

# If satisfied:
uv run memory-engine vault rotate \
  --old-key "$CURRENT_KEY" \
  --new-key "$NEW_KEY"
```

Expected output:

```
Rotated 42 vault entries in 3.2s.
Verification: all new entries decrypt with new key.
Old key will be accepted for a 24-hour grace window.
```

The "grace window" exists for rollback. The engine accepts both keys for 24 hours; after that, the old key stops working.

### 4. Update the environment variable

```bash
# In /opt/memory_engine/.env.local
sed -i "s|MEMORY_ENGINE_VAULT_KEY=.*|MEMORY_ENGINE_VAULT_KEY=$NEW_KEY|" /opt/memory_engine/.env.local
```

### 5. Release halt and restart

```bash
systemctl restart memory-engine
# The restart picks up the new key.

uv run memory-engine halt release --reason "vault rotation complete"
```

### 6. Verify

```bash
# Trigger a vault read via a retrieval that references a vault entry
curl -sX POST http://localhost:4000/v1/recall \
  -H 'content-type: application/json' \
  -d '{"persona_slug":"<slug>","query":"my AWS keys","lens":"self","top_k":1}'
```

Response should include the vault-referenced neuron; the outbound redactor will show `<vault:...>` placeholders (not the actual values — redactor does its job regardless of vault state).

To actually decrypt and see a value end-to-end, use the admin CLI:

```bash
uv run memory-engine vault show <vault_id> --admin --reason "post-rotation verification"
```

This writes an `admin_vault_read` event — audit trail of the verification.

### 7. Dispose of the old key

Wait 24 hours (the grace window). Then:

```bash
uv run memory-engine vault revoke-old-key --reason "24h post-rotation"
```

This permanently removes the old key's acceptance. Only the new key decrypts from now on.

Delete the old key from your password manager.

## Troubleshooting

**`vault rotate` reports "failed to decrypt entry N"** — the old key you provided doesn't match the entries. Either the key is wrong, or some entries were written with a different key (historical rotation gap). Investigate before proceeding; do not force-rotate over entries you can't decrypt, as they become permanently unreadable.

**Engine restart fails with "vault key missing"** — env var not set correctly. Check `.env.local` format (no quotes, no spaces around `=`).

**Retrieval returns neurons but value-resolving fails** — new key rotated but engine wasn't restarted, or restart didn't pick up env. Confirm `systemctl status memory-engine` shows the expected environment.

**Disaster: rotated but lost the new key** — recover from backup before the rotation. The backup's vault is encrypted with the pre-rotation key, which (if you didn't delete it) you can still use.

## Prevention

- Store the master key in a dedicated secret manager (AWS Secrets Manager, HashiCorp Vault, 1Password).
- Never the same key as backups. Vault encrypts secrets-in-use; backup-age encrypts data-at-rest. Compromise of one should not compromise the other.
- Document every rotation in `docs/runbooks/rotations.md`.
