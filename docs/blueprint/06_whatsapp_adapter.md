# Wiki v3 — WhatsApp MCP Adapter Specification

> Adapter spec for integrating a WhatsApp MCP server into a Wiki v3 deployment.
> **Not a blueprint revision.** This document is an implementation guide that depends on v0.0 + v0.1 + v0.2 + v0.3.
> The core promise: **no chat ever mixes information across counterparties.**

---

## 0. Scope and Locked Decisions

This adapter covers ingesting WhatsApp messages into a Wiki v3 memory system and sending twin-generated replies back out. It is intentionally narrow: it does not cover WhatsApp Business API, broadcast lists, or WhatsApp Web automation beyond what an MCP exposes.

Three decisions are locked and this document depends on them:

| # | Decision | Implication |
|---|---|---|
| 1 | **One MCP per persona.** Each digital twin has its own WhatsApp account and its own MCP process. | Persona isolation is enforced at the network boundary. An MCP's auth token only works for its bound persona. |
| 2 | **Groups are counterparties.** A WhatsApp group maps to a single `counterparty` with `external_ref = whatsapp-group:<jid>`. Individual members' messages in the group are not separately tracked. | Alice-in-group and Alice-in-1:1 are two counterparties. Cross-referencing requires the audited admin path. Accepted trade-off. |
| 3 | **Deletion policy per persona.** Configured in the persona's identity document. Each persona chooses whether WhatsApp "delete for everyone" events trigger protective forgetting, are ignored, or go to operator review. | No global default. Policy is visible in the identity document and auditable. |

The rest of this document is consequences of these three decisions.

---

## 1. The Cross-Chat Leakage Guarantee

The primary requirement — "no chat ever leaks information from another chat" — is delivered by a stack of four mechanisms, any one of which would catch the leak. Defense in depth is intentional.

| Layer | Mechanism | What it prevents |
|---|---|---|
| Storage | `counterparty_fact` neurons carry `counterparty_id`; `CHECK` constraint enforces presence | Schema-level partitioning — a counterparty fact without a counterparty is a DB error |
| Retrieval | SQL `WHERE counterparty_id = :active` in every recall query touching `counterparty_fact` | Wrong-counterparty neurons never enter the result set |
| Egress | Redactor verifies returned neurons' `counterparty_id` matches active `counterparty_context` before emission | Catches anything that slipped through upstream |
| Invariant | `no_cross_counterparty_leak` hard invariant (v0.3 §4.8); halts retrievals on any detected leak | Post-hoc detection stops further leaks until acknowledged |

What the WhatsApp adapter adds on top: ensuring `counterparty_context` is correctly set on every ingest and every recall, and ensuring outbound messages reach the right WhatsApp chat. Those are the two places where an adapter bug could bypass the guarantee.

---

## 2. MCP-to-Persona Binding

### 2.1 Registration

Each MCP is registered once per persona:

```
wiki-v3 mcp register \
  --persona randunu-twin \
  --mcp-name whatsapp-randunu \
  --mcp-pubkey ./mcp_whatsapp_randunu.pub
```

This creates:

- A row in `mcp_sources` linking the MCP identity to the persona.
- A bearer token, returned once, stored in the MCP process's environment.
- An accepted Ed25519 public key; the MCP signs every ingest envelope with its matching private key.

### 2.2 Schema

```sql
mcp_sources(
  id              BIGSERIAL PRIMARY KEY,
  persona_id      BIGINT NOT NULL REFERENCES personas(id),
  mcp_name        TEXT NOT NULL,
  pubkey          BYTEA NOT NULL,
  token_hash      BYTEA NOT NULL,              -- store hash; the token itself is shown once
  token_rotated_at TIMESTAMPTZ NOT NULL,
  active          BOOLEAN NOT NULL DEFAULT TRUE,
  last_seen       TIMESTAMPTZ,
  UNIQUE (persona_id, mcp_name)
);
```

Tokens rotate every 90 days (default, config). Rotation is a new token issued; the old token stays valid for a 24-hour overlap window so the MCP can swap without downtime.

### 2.3 Ingest authentication

Every POST to `/v1/ingest` from a WhatsApp MCP carries:

- `Authorization: Bearer <token>` — resolves to a specific `mcp_sources.id` and therefore a persona.
- `X-MCP-Signature: <ed25519_sig>` — signature over the canonicalized event body.

The ingest endpoint rejects requests where:

- Token does not resolve to an active `mcp_sources` row.
- Signature does not verify against the registered pubkey.
- The `persona_id` in the event body (if specified) disagrees with the token's persona.
- The `external_ref` prefix is not `whatsapp:` or `whatsapp-group:`.

These checks run before the normal ingest pipeline. A rejected request does not create an event.

### 2.4 Credential storage

The MCP needs WhatsApp access credentials (session data, device ID, encryption keys — whatever the underlying library requires). These live in the vault (§4.4):

- Sealed with `secretbox` at registration time.
- Unsealed by the MCP at process start via an authenticated `vault/unseal` call.
- Never logged, never embedded, never in retrieval output.

If the MCP's device session expires (WhatsApp forces re-scan), the operator re-registers; no data migration required because persona state is independent of session state.

---

## 3. Counterparty Creation and Identity

### 3.1 1:1 contacts

On first message from an unknown number:

```
MCP receives message from +94771234567 (name in contact list: "Alice")
  ↓
MCP posts to /v1/ingest with:
  external_ref: "whatsapp:+94771234567"
  display_name_hint: "Alice"
  content: "..."
  ↓
wiki-v3:
  counterparty = lookup_or_create(persona_id, "whatsapp:+94771234567")
  if created:
    counterparty.display_name = "Alice"
    counterparty.first_seen = now()
  event appended with counterparty_id set
```

Phone number is canonicalized at ingest: strip spaces, dashes, parentheses; ensure leading `+<country>`. This prevents `whatsapp:+94771234567` and `whatsapp:94771234567` from becoming two counterparties.

### 3.2 Groups

On first message from an unknown group:

```
MCP receives message in group "Acme Team" (jid: 1234567890-1699999999@g.us)
  ↓
MCP posts:
  external_ref: "whatsapp-group:1234567890-1699999999@g.us"
  display_name_hint: "Acme Team"
  content: "..."
  sender_hint: "whatsapp:+94771234567"           // metadata only; does NOT create a sub-counterparty
  ↓
wiki-v3:
  counterparty = lookup_or_create("whatsapp-group:...")
  event appended with this counterparty_id
  event payload includes sender metadata for future reference
```

The `sender_hint` is stored in the event's payload for audit and future admin queries but **does not create a counterparty for the sender** and does not influence retrieval. Alice sending a message in the Acme group is indistinguishable at retrieval time from Bob sending one; both are "messages in the Acme Team counterparty." This is the price of the group-as-counterparty simplification and was an explicit decision.

If Alice later sends a 1:1 message, that creates a separate counterparty (`whatsapp:+94771234567`). The two counterparties are not automatically linked. An operator who wants to link them uses the admin merge path — which is logged, reversible, and should be rare.

### 3.3 Group metadata sync

The MCP may optionally push group membership updates to wiki-v3 via `/v1/counterparty_metadata`. This populates the counterparty's `relationship` JSONB with:

- Current member list (for audit; members are not separate counterparties).
- Group subject changes.
- Admin/owner status.

Membership data is stored at the counterparty level only. Using it to reverse-engineer "who said what" across groups is not supported by the recall API.

### 3.4 Forwarded messages

A forwarded message attributes to the forwarder, not the original author:

- `counterparty_id` = the person who forwarded it to the twin
- Event payload carries `forwarded_from: <original_sender_if_known>` as audit metadata
- Entities mentioned in the forwarded content may be extracted as `domain_fact` neurons during consolidation, but not as `counterparty_fact` for the original author

This is the correct privacy behavior: the twin learned the information from Alice, not from the original author. Treating it as counterparty-fact-about-the-original would silently expand who the twin "knows about."

---

## 4. Ingest Pipeline (Inbound)

End-to-end, a WhatsApp message becomes an event:

```
WhatsApp client (via MCP library: baileys, whatsmeow, etc.)
    │ raw message
    ▼
MCP process
    │ normalize to canonical envelope:
    │   { external_ref, content, timestamp, attachments[], message_id,
    │     group_sender_hint?, forwarded_from?, reply_to_message_id? }
    ▼
MCP signs envelope (Ed25519)
    ▼
POST /v1/ingest
    │ header: Authorization + X-MCP-Signature
    │ body: envelope + persona_id (implicit via token)
    ▼
wiki-v3 ingest API
    │
    ├─► verify token + signature + persona match  → reject on fail
    ├─► canonicalize external_ref (phone normalize, jid normalize)
    ├─► lookup_or_create(counterparty)
    ├─► classify as counterparty_fact with counterparty_id
    ├─► scope defaults from identity.scope_defaults.counterparty_fact
    ├─► PII scrubber (emails, numbers, OTPs, secrets)
    ├─► injection detector (tag if suspicious; never reject)
    ├─► compute content_hash
    ├─► check tombstones → reject on match
    ├─► check idempotency_key (MCP should send message_id as idempotency key)
    ▼
events.append(event)
working_memory.ingest(event)
```

Notes:

- **Attachments.** Media files (images, voice notes, documents) are stored separately, encrypted at rest with per-persona key. The event payload holds a reference, not the bytes. Voice notes are transcribed by a separate service (Whisper-class) and the transcript becomes the event content; the original audio stays in encrypted blob storage with its own retention policy.

- **Replies and threads.** WhatsApp reply-to metadata becomes `parent_event_id` when the referenced message exists in the log. If the referenced message is from before the twin's registration, `parent_event_id` is NULL and the reply stands alone.

- **Read receipts.** Not ingested by default. If ingested (config opt-in), they become low-confidence `counterparty_fact` neurons like "Alice read the message at T."

- **Typing indicators, online status.** Not ingested. Too noisy, too privacy-sensitive.

---

## 5. Outbound Pipeline (Twin-Generated Replies)

Outbound is where the most can go wrong: the twin says something it shouldn't. The pipeline is:

```
Downstream agent/LLM decides to send a message
    │ { persona, counterparty_context, proposed_content }
    ▼
POST /v1/persona_output
    ▼
wiki-v3 output pipeline
    │
    ├─► [1] identity check
    │       load persona.identity
    │       check content against non_negotiables  → HARD BLOCK on violation
    │       check content against values            → FLAG on contradiction, continue
    │
    ├─► [2] self-contradiction check
    │       embed proposed_content
    │       vector search prior persona_output to same counterparty
    │       if high-similarity contradicting prior output → FLAG, continue
    │
    ├─► [3] privacy check
    │       scan content for vault://* refs        → BLOCK if present
    │       scan content for PII patterns          → re-scrub
    │       verify any entity references don't leak other counterparty_fact
    │
    ├─► [4] egress redactor
    │       enforce counterparty_context on any embedded retrieved data
    │       return clean content + policy_applied metadata
    │
    ├─► [5] append persona_output event
    │       kind: self_fact (it's the twin's own utterance)
    │       counterparty_context: <ref>
    │       content: redacted content
    │
    ▼
Return to caller: { approved: true, content, policy_applied[] }
OR:               { approved: false, reason, violated_rule }
    ▼
MCP receives approved content
    ▼
MCP sends via WhatsApp library
    ▼
MCP posts back to /v1/output_delivered with message_id from WhatsApp
    (updates the persona_output event with delivery confirmation)
```

**Critical separation:** wiki-v3 approves or rejects; the MCP sends. Reasons:

- wiki-v3 never holds WhatsApp credentials after unseal-on-start.
- wiki-v3 never makes outbound network calls to Meta's infrastructure.
- If the WhatsApp session is broken, approved messages queue in the MCP, not wiki-v3.
- wiki-v3 can approve or reject based on policy; delivery is an orthogonal concern.

**If approval fails:** the proposed content is not stored as a persona_output event. The rejection is logged with reason, visible to the operator. The downstream caller decides whether to regenerate, rephrase, or escalate.

**If approval succeeds but delivery fails:** the persona_output event exists but is marked undelivered. The MCP retries. If retries are exhausted, the event is quarantined and operator is alerted — the twin thinks it said something that was never received, which is a state worth flagging.

---

## 6. Deletion Policy

### 6.1 Configuration in identity document

Extends the identity JSON schema from v0.3 §21.2:

```json
{
  "role": "...",
  "values": ["..."],
  "non_negotiables": ["..."],
  "scope_defaults": {...},
  "policies": {
    "whatsapp_deletion": {
      "inbound": "ignore",
      "outbound": "honor",
      "per_counterparty_overrides": {
        "whatsapp:+94771111111": { "inbound": "honor" }
      }
    },
    "whatsapp_ephemeral": {
      "mode": "respect",
      "retention_override_days": 7
    },
    "whatsapp_read_receipts": {
      "ingest": false
    }
  }
}
```

`inbound` policy: what happens when a counterparty deletes a message they sent to the twin.

- `honor`: emit `protective_forgetting` for the message's content_hash. Message vanishes from memory.
- `ignore`: emit `deletion_observed` event (the twin remembers what was said and also that it was later unsent — both are facts).
- `review`: emit `deletion_pending_review` event, route to operator queue.

`outbound` policy: what happens when the twin-sent message is deleted (either by the twin or by WhatsApp's "delete for everyone"):

- `honor`: the twin's `persona_output` event is tombstoned and content-forgotten.
- `ignore`: the twin remembers what it said even if the recipient no longer sees it.

Default: `inbound=ignore, outbound=honor`. This matches human behavior (humans remember what others said to them; humans own what they said and can retract).

### 6.2 MCP deletion handling

When the MCP observes a deletion event:

```
MCP receives WhatsApp "revoke" event for message_id X
    ↓
MCP posts to /v1/deletion:
    { external_ref, message_id: X, deletion_direction: inbound|outbound }
    ↓
wiki-v3:
    look up original event by (persona_id, counterparty_id, message_id)
    load persona.identity.policies.whatsapp_deletion
    resolve policy (check per_counterparty_overrides first, then direction default)
    apply action:
      honor: protective_forgetting(content_hash) + cascade cleanup
      ignore: append deletion_observed event
      review: append deletion_pending_review event + queue
```

Deletion events are themselves audit records. Even in `honor` mode, the fact that a deletion occurred is logged in `healing_log` (not the content that was deleted).

### 6.3 Ephemeral messages

WhatsApp's disappearing messages have a set TTL (24h, 7d, 90d). On ingest, if the MCP reports the message as ephemeral:

```
event.payload.ephemeral_ttl_seconds = <whatsapp_ttl>
event.payload.ephemeral_expires_at = ingest_ts + ttl
```

If persona policy `whatsapp_ephemeral.mode = respect`:

- The derived neuron inherits `tier = ephemeral` (a config-defined tier with fast decay).
- Pruner runs at `ephemeral_expires_at + retention_override_days`.
- After prune, a tombstone prevents re-ingestion if the message somehow resurfaces.

If persona policy is `ignore`, ephemeral messages are treated like normal messages. Compliant behavior is `respect`.

---

## 7. The MCP Trust Model

The MCP is untrusted code with privileged access. Assume it can be:

- Buggy (library defects)
- Compromised (supply chain attack on baileys/whatsmeow)
- Malicious (operator misconfiguration or hostile contributor)

### 7.1 Boundaries

| Concern | Mitigation |
|---|---|
| MCP forges sender identity | MCP must report sender external_ref; wiki-v3 cannot independently verify. Detection: anomalies in counterparty behavior, flagged by drift counter (§23). Mitigation: treat MCP events as observations, not ground truth, for high-sensitivity scopes. |
| MCP injects fabricated history with old timestamps | MCP-reported timestamps are informational; server timestamp is authoritative for ordering. Historical backfill is a separate `/v1/ingest/historical` endpoint that requires operator approval per batch. |
| MCP claims events for a different persona | Token-to-persona binding rejects this at auth time. |
| MCP leaks vault credentials | Vault unseal is single-use at process start; credentials held in memory by MCP, never re-requested. Process restart requires operator action. |
| MCP queries recall to exfiltrate memory | MCPs have ingest-only tokens. Recall requires a separate token not issued to MCPs. |

### 7.2 MCP capability scopes

Tokens have scopes. A WhatsApp MCP's token scopes are:

- `ingest:counterparty_fact` — can ingest events tagged as counterparty facts.
- `counterparty:create` — can cause new counterparty rows to exist.
- `output:deliver_confirmation` — can update persona_output delivery status.
- `deletion:report` — can report deletion events.

It does not have:

- `recall:*` — cannot query memory.
- `persona:modify` — cannot change identity.
- `admin:*` — cannot access admin paths.

If the MCP needs to query (e.g., to personalize outgoing messages via an LLM), that's done by a separate recall client that holds its own token; the MCP does not proxy memory queries.

### 7.3 Audit

Every MCP-sourced event logs:

- MCP identity (`mcp_sources.id`)
- Token fingerprint (first 8 chars)
- Signature verification result
- Source IP (if MCP is remote)
- Canonical envelope hash

This makes post-hoc detection of a bad MCP possible. If the operator notices a counterparty has wildly inconsistent claimed identities, the audit trail shows which MCP ingested them.

---

## 8. Configuration Reference

Full config additions for the WhatsApp adapter:

```toml
[adapters.whatsapp]
enabled = true

[adapters.whatsapp.mcp]
registration_required = true              # decision: no anonymous MCPs
token_ttl_days = 90                       # guess
rotation_overlap_hours = 24               # guess
signature_required = true                 # decision
allowed_signature_algo = "ed25519"

[adapters.whatsapp.ingest]
canonicalize_phone = true                 # decision: prevents counterparty fragmentation
allow_media = true
media_storage = "local-encrypted"         # local-encrypted | s3-encrypted
voice_transcription = true
voice_transcription_model = "local"       # decision: privacy default
max_message_age_days = 365                # guess; reject historical-backfill beyond

[adapters.whatsapp.outbound]
approval_required = true                  # decision: non-negotiable
deliver_timeout_seconds = 30
retry_on_delivery_failure = 3
quarantine_on_persistent_failure = true

[adapters.whatsapp.deletion]
# Persona-level overrides in identity document.
# These are system-wide fallbacks.
default_inbound = "ignore"
default_outbound = "honor"

[adapters.whatsapp.ephemeral]
default_mode = "respect"
grace_period_days = 0                     # decision: honor WhatsApp TTL exactly

[adapters.whatsapp.read_receipts]
default_ingest = false                    # decision: privacy default
```

---

## 9. Implementation Checklist

Concrete micro-tasks for building the adapter. Roughly **3–4 weeks** half-time to reference-complete, on top of the v0.3 roadmap.

### 9.1 wiki-v3 side

| # | Task | Acceptance |
|---|---|---|
| W1 | `mcp_sources` schema + migrations | Schema tests pass |
| W2 | `/v1/ingest` token + signature verification | Forged requests rejected, valid ones accepted |
| W3 | Phone number canonicalizer | All variants of one number map to one counterparty |
| W4 | JID canonicalizer for groups | Group jids normalized |
| W5 | `/v1/persona_output` endpoint + approval pipeline | Non-negotiable violations hard-blocked |
| W6 | `/v1/deletion` endpoint + policy dispatch | All three modes (honor/ignore/review) tested |
| W7 | Identity policy schema extension | Old identity docs parse with defaults; new docs validate |
| W8 | Ephemeral tier + pruner integration | Ephemeral messages disappear on TTL |
| W9 | MCP CLI: `mcp register`, `mcp rotate`, `mcp revoke` | Lifecycle works end-to-end |
| W10 | Audit log of MCP-sourced events | Every event traceable to its MCP identity |

### 9.2 MCP side (reference implementation)

| # | Task | Acceptance |
|---|---|---|
| M1 | Pick library (baileys/whatsmeow/other) + justify choice | One library committed, dependencies vendored |
| M2 | Session persistence + vault integration | MCP restart resumes session from vault without re-scan |
| M3 | Message → canonical envelope mapper | All message types (text, media, reply, forward, group) covered |
| M4 | Ed25519 signing | Every outbound POST signed |
| M5 | Idempotency: use WhatsApp message_id as key | Retries don't duplicate |
| M6 | Outbound queue | Approved messages buffer on WhatsApp disconnect, drain on reconnect |
| M7 | Deletion event handler | All three policies tested in integration |
| M8 | Graceful shutdown | SIGTERM drains queue, commits session state, exits clean |
| M9 | Health endpoint | wiki-v3 can probe MCP liveness |
| M10 | Observability | Metrics on ingested, approved, rejected, delivered, queued |

### 9.3 End-to-end tests

| # | Test | Pass criterion |
|---|---|---|
| T1 | 1:1 message ingest + recall with counterparty lens | Recall returns only that counterparty's facts |
| T2 | Group message ingest | Group counterparty created; sender hint in payload but no sender-counterparty |
| T3 | Cross-counterparty attempt: ingest as Alice, recall with Bob's context | Zero Alice neurons in results |
| T4 | Approval: non-negotiable violation | Output blocked, reason surfaced |
| T5 | Approval: self-contradiction | Output flagged, reason surfaced, still delivered |
| T6 | Deletion: honor mode | Content forgotten, tombstone set |
| T7 | Deletion: ignore mode | Original fact preserved, deletion event logged |
| T8 | Ephemeral message | Neuron auto-prunes at TTL |
| T9 | MCP token rotation | Old token valid in overlap, rejected after |
| T10 | MCP compromise simulation | Fake events rejected, legitimate events unaffected |
| T11 | Prompt injection in message content | Event ingested, injection_attempt flag set, retrieval never returns other-counterparty neurons |
| T12 | Forwarded message | Counterparty = forwarder, not original author |

T3 and T11 are the tests that directly verify the leakage guarantee. They are non-negotiable for release.

---

## 10. What This Adapter Does Not Do

- **Contact deduplication across channels.** If Alice also has Telegram and email, those are separate counterparties until operator merges. The WhatsApp adapter does not reach into other adapters' counterparty space.
- **End-to-end encryption with the LLM.** Messages are decrypted by WhatsApp on the MCP's device. Wiki-v3 sees plaintext. If that is not acceptable, this adapter is the wrong abstraction.
- **Message scheduling.** The twin approves and emits; the MCP sends immediately. Scheduled sends are a caller-side feature.
- **Multi-device coordination.** WhatsApp supports multi-device, but this MCP runs on one device. Multi-device integration is out of scope.
- **Business API features.** Templates, broadcasts, opt-in flows. This is a personal/consumer WhatsApp adapter.

If any of these are needed, they are separate adapters or separate modules. This one does one thing and does it with bounded trust.

---

## 11. Relationship to the Rest of the Blueprint

This document depends on:

- **v0.3 §21** — Personas (the MCP binds to one)
- **v0.3 §22** — Counterparties (WhatsApp contacts and groups map to these)
- **v0.3 §4.5** — Three neuron kinds (everything ingested from WhatsApp is `counterparty_fact`)
- **v0.3 §4.7** — Retrieval lenses (calls should set `counterparty_context` to the active WhatsApp chat)
- **v0.3 §4.8** — Hard invariants (no_cross_counterparty_leak covers this adapter)
- **v0.3 §23** — Identity protocol (outbound approval pipeline)
- **v0.2 §19** — Cost model (voice transcription and inbound classification are LLM calls)
- **v0.1 §4.4** — Vault (WhatsApp session credentials)

This document does not modify any of those. It is an implementation of them for one channel.

---

## 12. Remaining Open Questions

These are honest gaps for the operator to resolve before production:

1. **Historical backfill policy.** When a twin is newly deployed against an existing WhatsApp account, importing historical messages is tempting but creates backdated counterparty relationships the twin never actually had. Recommend: don't backfill; let memory grow from live traffic. If backfill is required, flag all backfilled events with a `provenance.historical_import: true` marker and weigh them less.

2. **Voice transcription model choice.** Local (Whisper) is private but slow and accuracy-bounded; remote (commercial ASR) is faster and more accurate but ships audio off-device. Pick per persona. Default local.

3. **Rate limiting.** WhatsApp may rate-limit outbound. The MCP should buffer; wiki-v3's approval pipeline doesn't care. Decide buffer size and behavior on overflow.

4. **Account ban resilience.** Automated WhatsApp use risks account bans. This is a product-level risk, not a technical one. Document it, don't pretend it away.

5. **Archival policy for WhatsApp media.** High-volume chats with media accumulate fast. Media archival rules are separate from event archival (§20). Spec pending; rough default: media older than 180 days moves to cold storage, references in events remain valid but cold-restore has a latency cost.

These are gaps, not contradictions. Resolve at implementation time with actual data in front of you.

---

*End of WhatsApp MCP adapter spec. This document is an implementation guide, not a design revision. If a second channel adapter (Slack, Telegram, Signal) is built later, the pattern transfers: per-persona MCP, token-bound, signed events, approval pipeline, deletion policy in identity. The shape is reusable. The specifics are WhatsApp's.*
