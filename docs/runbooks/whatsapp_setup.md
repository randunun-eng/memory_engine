# Runbook: WhatsApp Business API setup

> Required before Phase 5 can deploy. End-to-end provisioning from Meta Business account to an engine-ready webhook.

## Prerequisites

- A Meta Business Manager account (https://business.facebook.com).
- A Facebook app in developer mode or published.
- A phone number that is NOT already on WhatsApp (personal or business). Meta requires a dedicated number per WhatsApp Business API registration.
- A domain with HTTPS for the webhook. `ngrok` or `cloudflared` tunnel works for development; production needs a real domain.

## One-time Meta setup

### 1. Create a WhatsApp Business App

1. Visit https://developers.facebook.com/apps.
2. Create App → Business → give it a name (e.g., `memory_engine_wa_<persona>`).
3. Add product: WhatsApp → Set up.
4. Link to a Business Manager account.

### 2. Register the phone number

1. In the WhatsApp settings within the app, click "Add phone number."
2. Provide the number. Meta sends SMS/voice verification.
3. Complete display name approval (Meta reviews; can take 24h–7d).
4. Note the **Phone Number ID** — a ~15-digit string. You will pass this to the engine.

### 3. Generate a system user access token

1. Meta Business settings → Users → System users → Add.
2. Name it `memory_engine_<persona>`, role Admin.
3. Generate a token for this system user with scopes: `whatsapp_business_messaging`, `whatsapp_business_management`.
4. Set the expiration to "Never" for a long-lived token, or 60 days with a rotation reminder.
5. Save the token in your password manager. This token is what the engine uses to send messages and read media.

### 4. Configure the webhook

1. In the app's WhatsApp settings → Configuration → Webhook → Edit.
2. Callback URL: `https://<your-domain>/v1/wa/webhook/<persona_slug>`.
3. Verify token: a random string you generate. Example: `openssl rand -hex 16`. Save this.
4. Subscribe to fields: at minimum `messages`. Add `message_status` for delivery receipts.

Meta will hit the callback URL with a verification challenge. The engine's `GET /v1/wa/webhook/<slug>` responds to this automatically using the stored verify token.

## Engine side

### 1. Register the MCP

```bash
uv run memory-engine mcp register <persona_slug> whatsapp \
  --name primary \
  --wa-phone-number-id <META_WA_PHONE_NUMBER_ID> \
  --wa-access-token <META_WA_ACCESS_TOKEN> \
  --verify-token <YOUR_VERIFY_TOKEN>
```

The command:
- Generates a new Ed25519 keypair for the MCP.
- Stores the public key in `mcp_sources`.
- Prints the private key **once** — save it in your password manager. Losing it means re-registering the MCP.
- Stores the access token and verify token encrypted in the engine's secret vault.

### 2. Verify the webhook

```bash
uv run memory-engine wa verify-webhook <persona_slug>
```

This sends a synthetic verification request to your running engine's webhook URL and confirms the response. If your engine isn't reachable from the internet yet, skip this step and configure in Meta's UI, which will attempt a real verification.

### 3. Test send

```bash
uv run memory-engine wa test-send <persona_slug> \
  --to +<country-code><number> \
  --text "Test from memory_engine."
```

You should receive the message on the target number. If not, check:
- Access token validity (`curl` the Meta Graph API directly).
- Phone number ID matches.
- Target number has opted in (WA requires prior consent; see Meta's 24-hour messaging window rules).

## Production deployment

- Webhook URL must be publicly reachable with a valid TLS cert. Let's Encrypt is fine.
- The domain should be stable. Changing it requires reconfiguring the Meta webhook (a few clicks but disruptive).
- Rate limits: Meta throttles per phone number. Start at < 1000 messages/day for a new registration; Meta gradually raises the limit based on quality signals.

## Troubleshooting

**Webhook verification fails** — verify token mismatch. Confirm the token you entered in Meta exactly matches the one stored by `mcp register`.

**Messages arrive but engine doesn't process them** — check the FastAPI logs. Common cause: `x-hub-signature-256` validation failing because app secret wasn't passed correctly. The `mcp register` command stores the app secret from your Meta app settings; if you rotated the secret in Meta, re-register.

**Send succeeds in Meta's Graph API but fails via engine** — check that the engine's outbound approval pipeline isn't blocking. Look for `outbound_blocked` events in the log.

**Meta disabled the number** — usually due to quality signals (too many blocks by recipients). Check Meta's dashboard; follow their appeal process. Consider whether your twin is behaving in ways that drive blocks.

## Rotation

Separate runbook: `docs/runbooks/mcp_rotation.md`.
