# Phase 4 — Identity + Counterparties

> **Status:** Blocked on Phase 3.
>
> **Duration:** 3 weeks (half-time solo).
>
> **Acceptance criterion:** A signed identity document loads into a persona. Every outbound draft passes through the non-negotiables check; violations are hard-blocked with a logged event. Tone profiles adapt per counterparty based on the last 50 messages. An identity drift flag is raised when an LLM extraction contradicts a non-negotiable.

---

## Goal

The persona knows who it is and what it will not do. Counterparties get individualized tone. The blueprint's "pillar hierarchy" (rule 13: privacy > counterparty > persona > factual) becomes an enforced outbound pipeline.

Phase 4 is where the digital twin becomes recognizably "a twin of someone" rather than a generic memory engine. It's also where refusal becomes first-class — the twin must say no when it should.

---

## Prerequisites

- Phase 3 complete. Healer and halt operational.
- Consolidator producing neurons reliably.

---

## Schema changes

Migration 004. See `docs/SCHEMA.md` → Migration 004.

- `identity_drift_flags` — candidate extractions that contradict identity.
- `tone_profiles` — per-counterparty tone analysis (JSON blob).

No changes to existing tables. Identity lives in `personas.identity_doc` (TEXT column added in migration 001).

---

## File manifest

### Identity

- `src/memory_engine/identity/__init__.py`
- `src/memory_engine/identity/document.py` — YAML schema, Pydantic model, signature verification.
- `src/memory_engine/identity/loader.py` — load from file or DB, cache in memory.
- `src/memory_engine/identity/drift.py` — drift detection on incoming neurons.

### Counterparties

- `src/memory_engine/core/counterparties.py` — CRUD for counterparty table.
- `src/memory_engine/core/tone.py` — tone profile analysis and lookup.

### Outbound

- `src/memory_engine/outbound/__init__.py`
- `src/memory_engine/outbound/approval.py` — the pipeline: redactor → non-negotiables → identity check → deliver.
- `src/memory_engine/outbound/redactor.py` — pattern-based redaction.
- `src/memory_engine/outbound/patterns.py` — redaction patterns, externalized.
- `src/memory_engine/outbound/nonneg.py` — non-negotiable evaluation.

### CLI additions

- `src/memory_engine/cli/identity.py` — `memory-engine identity load <persona> <path>`, `identity show <persona>`, `identity verify <persona>`.
- `src/memory_engine/cli/counterparty.py` — `memory-engine counterparty list`, `counterparty tone <persona> <ref>`.

### Tests

- `tests/integration/test_phase4.py`
- `tests/invariants/test_phase4.py`
- `tests/fixtures/identity_documents/` — sample YAML identity docs.

---

## Identity document schema

YAML, signed by the persona owner. The engine verifies the signature before loading.

```yaml
# identity.yaml
schema_version: "1.0"
persona_slug: "sales_twin"
owner: "randunu@example.com"
issued_at: "2026-04-16T00:00:00Z"

role:
  title: "Sales Development Representative"
  domain: "B2B SaaS infrastructure"
  responsibilities:
    - "Qualify inbound leads"
    - "Schedule discovery calls"
    - "Maintain CRM hygiene"

values:
  - "Never pressure a prospect who says no."
  - "Be transparent about pricing when asked directly."
  - "Do not make commitments on deliverables outside the sales team's authority."

non_negotiables:
  - id: nn_1
    rule: "Never send pricing details to anyone not already a qualified lead."
    trigger_patterns:
      - "pricing"
      - "cost"
      - "quote"
      - "per seat"
    evaluator: "llm"           # or "pattern" for simple cases
    severity: "block"          # 'block' = hard stop; 'flag' = log only
  - id: nn_2
    rule: "Do not commit to product roadmap items without engineering approval."
    evaluator: "llm"
    severity: "block"
  - id: nn_3
    rule: "Do not discuss compensation, hiring plans, or internal staffing."
    evaluator: "llm"
    severity: "block"

tone_defaults:
  formality: "professional"
  length_preference: "concise"
  emoji: false

boundaries:
  topics_off_limits:
    - "Personal opinions on competitors"
    - "Internal strategy discussions"
  topics_deflect:
    - "Technical architecture details" # defer to engineering

signature: "ed25519:MC0CAQAwBQYDK2VwBCIEIH..."    # base64 over the unsigned YAML body
```

Pydantic model enforces the schema:

```python
# src/memory_engine/identity/document.py

class NonNegotiable(BaseModel):
    id: str
    rule: str
    trigger_patterns: list[str] = Field(default_factory=list)
    evaluator: Literal["llm", "pattern"] = "llm"
    severity: Literal["block", "flag"] = "block"


class ToneDefaults(BaseModel):
    formality: Literal["casual", "professional", "formal"] = "professional"
    length_preference: Literal["concise", "moderate", "detailed"] = "moderate"
    emoji: bool = False


class Boundaries(BaseModel):
    topics_off_limits: list[str] = Field(default_factory=list)
    topics_deflect: list[str] = Field(default_factory=list)


class IdentityDocument(BaseModel):
    schema_version: Literal["1.0"]
    persona_slug: str
    owner: str
    issued_at: datetime
    role: Role
    values: list[str]
    non_negotiables: list[NonNegotiable]
    tone_defaults: ToneDefaults
    boundaries: Boundaries
    signature: str    # verified separately
```

### Signature scheme

Identity documents are signed by the persona *owner*, not by an MCP. A separate keypair. Key registration is a one-time CLI action:

```bash
memory-engine identity init-owner <persona> --email owner@example.com
# prints public key; operator records it outside the engine.
# operator provides the public key when loading the identity document:
memory-engine identity load <persona> <yaml-path> --owner-pubkey <b64>
```

Verification: canonical form of the YAML body (sorted keys, stable whitespace) signed with Ed25519.

---

## Outbound pipeline

Every outbound message flows through `approval.pipeline()`:

```
                        Draft (from retrieval + generation)
                                     │
                                     ▼
                    ┌────────────────────────────────┐
                    │ 1. Privacy redactor            │
                    │    - strip cross-counterparty  │
                    │    - strip PII patterns        │
                    │    - strip vault references    │
                    └──────────┬─────────────────────┘
                               │
                               ▼
                    ┌────────────────────────────────┐
                    │ 2. Non-negotiables check       │
                    │    - Pattern match short-circ  │
                    │    - LLM evaluator for complex │
                    │    - Block severity halts      │
                    └──────────┬─────────────────────┘
                               │
                               ▼
                    ┌────────────────────────────────┐
                    │ 3. Identity alignment          │
                    │    - Boundary topics deflected │
                    │    - Tone adjusted per profile │
                    └──────────┬─────────────────────┘
                               │
                               ▼
                    ┌────────────────────────────────┐
                    │ 4. Deliver via adapter (P5)    │
                    │    - MCP signs outbound event  │
                    │    - message_out logged        │
                    └────────────────────────────────┘
```

Any stage can block. Blocked messages produce an `outbound_blocked` event with the stage and reason; they are not delivered.

```python
async def approve_outbound(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    counterparty_id: int,
    draft: str,
    retrieval_trace_id: int | None,
) -> ApprovalResult:
    """Run the outbound approval pipeline. Returns approved, redacted content, or block reason."""
    identity = await load_identity(conn, persona_id)
    counterparty = await get_counterparty(conn, counterparty_id)

    # Stage 1: Redact
    redacted = await redact(
        draft,
        persona_id=persona_id,
        active_counterparty=counterparty,
        vault=await load_vault_index(conn, persona_id),
    )
    if redacted != draft:
        log(event="outbound_redacted", persona_id=persona_id, counterparty_id=counterparty_id)

    # Stage 2: Non-negotiables
    for nn in identity.non_negotiables:
        verdict = await evaluate_nonneg(nn, redacted, identity, persona_id)
        if verdict.violates and nn.severity == "block":
            await emit_blocked_event(conn, persona_id, counterparty_id, stage="nonneg", rule=nn.id, reason=verdict.reason)
            return ApprovalResult.blocked(stage="nonneg", reason=verdict.reason, rule_id=nn.id)

    # Stage 3: Identity alignment
    aligned = await align_with_identity(redacted, identity, counterparty)

    # Stage 4: Not delivered here; adapter in Phase 5 takes the approved draft
    return ApprovalResult.approved(content=aligned)
```

---

## Non-negotiable evaluator

Two evaluator types. Pattern is fast; LLM is robust.

```python
async def evaluate_nonneg(
    nn: NonNegotiable,
    draft: str,
    identity: IdentityDocument,
    persona_id: int,
) -> NonNegVerdict:
    # Fast path: pattern evaluator
    if nn.evaluator == "pattern":
        for pattern in nn.trigger_patterns:
            if re.search(pattern, draft, re.IGNORECASE):
                return NonNegVerdict(violates=True, reason=f"matched pattern {pattern!r}")
        return NonNegVerdict(violates=False, reason=None)

    # LLM evaluator: dispatched via policy plane
    if nn.trigger_patterns:
        # Short-circuit if no trigger pattern hit — LLM call is expensive
        if not any(re.search(p, draft, re.IGNORECASE) for p in nn.trigger_patterns):
            return NonNegVerdict(violates=False, reason="no_trigger")

    result = await dispatch(
        "nonneg_judge",
        persona_id=persona_id,
        context={
            "draft": draft,
            "rule": nn.rule,
            "role": identity.role.title,
            "role_domain": identity.role.domain,
        },
    )
    return NonNegVerdict(violates=result.violates, reason=result.reason)
```

Register the `nonneg_judge` site in `src/memory_engine/policy/sites.py`. Prompt template at `src/memory_engine/policy/prompts/nonneg_judge.v1_0_0.md`.

---

## Redactor

Pattern-based first pass; cross-counterparty filter second.

```python
# src/memory_engine/outbound/patterns.py

REDACTION_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("email_pattern", re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b"), "[email redacted]"),
    ("phone_pattern", re.compile(r"\b(?:\+?\d{1,3}[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"), "[phone redacted]"),
    ("ssn_pattern", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn redacted]"),
    ("credit_card_pattern", re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[card redacted]"),
    # ... extensible
]


async def redact(
    text: str,
    *,
    persona_id: int,
    active_counterparty: Counterparty,
    vault: dict[str, str],
) -> str:
    # 1. Pattern redaction (except for the active counterparty's own values)
    allowed = {active_counterparty.external_ref}
    if active_counterparty.display_name:
        allowed.add(active_counterparty.display_name)

    for name, pattern, replacement in REDACTION_PATTERNS:
        def check(match):
            if match.group(0) in allowed:
                return match.group(0)
            return replacement
        text = pattern.sub(check, text)

    # 2. Cross-counterparty name redaction
    other_counterparties = await list_counterparties_other_than(persona_id, active_counterparty.id)
    for cp in other_counterparties:
        if cp.display_name and cp.display_name in text:
            text = text.replace(cp.display_name, "[other party]")

    # 3. Vault value redaction (should never appear, but defence-in-depth)
    for vault_value in vault.values():
        if vault_value and vault_value in text:
            text = text.replace(vault_value, "[vaulted]")

    return text
```

---

## Tone profiles

Per-counterparty analysis of the last 50 messages. Updated on every 10 new messages (cheap enough to be frequent).

```python
async def refresh_tone_profile(
    conn: aiosqlite.Connection,
    counterparty_id: int,
) -> ToneProfile:
    events = await fetch_recent_messages(conn, counterparty_id, limit=50)
    result = await dispatch(
        "analyze_tone",
        persona_id=events[0].persona_id if events else 0,
        context={"messages_text": _serialize_messages(events)},
    )
    await conn.execute("""
        INSERT INTO tone_profiles (counterparty_id, profile_json, analyzed_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT (counterparty_id) DO UPDATE SET
            profile_json = excluded.profile_json,
            analyzed_at = excluded.analyzed_at
    """, (counterparty_id, json.dumps(result.profile)))
    await conn.commit()
    return result.profile
```

Tone profile JSON shape:

```json
{
  "formality_score": 0.72,
  "average_message_length_words": 18,
  "emoji_usage": "low",
  "preferred_greeting": "Hi",
  "preferred_signoff": "Thanks",
  "response_time_median_minutes": 45
}
```

---

## Identity drift detection

When the consolidator extracts a `self_fact`, check it against identity:

```python
async def check_identity_drift(
    conn: aiosqlite.Connection,
    candidate: NeuronCandidate,
) -> DriftVerdict:
    """Flag if a self_fact candidate contradicts identity role, values, or non-negotiables.

    Does not block extraction; flags for operator review.
    """
    if candidate.kind != "self_fact":
        return DriftVerdict(drift=False)

    identity = await load_identity(conn, candidate.persona_id)

    result = await dispatch(
        "identity_drift_check",
        persona_id=candidate.persona_id,
        context={
            "candidate": candidate.content,
            "role": identity.role.dict(),
            "values": identity.values,
            "non_negotiables_rules": [nn.rule for nn in identity.non_negotiables],
        },
    )

    if result.drift:
        await conn.execute("""
            INSERT INTO identity_drift_flags
                (persona_id, flag_type, candidate_text, flagged_at)
            VALUES (?, ?, ?, datetime('now'))
        """, (candidate.persona_id, result.flag_type, candidate.content))
        await conn.commit()

    return result
```

Flags surface in `memory-engine identity drift list <persona>`. Operator reviews and either accepts (the candidate lands normally), rejects (discard), or escalates (halt pending investigation).

---

## Tests

### Integration (tests/integration/test_phase4.py)

```
test_identity_document_loads_from_file
test_identity_signature_verification
test_nonneg_pattern_blocks_outbound
test_nonneg_llm_blocks_outbound
test_outbound_blocked_event_logged
test_redactor_strips_email
test_redactor_strips_phone
test_redactor_preserves_active_counterparty_email
test_tone_profile_produced_from_messages
test_tone_profile_updates_on_new_messages
test_identity_drift_flags_contradicting_self_fact
test_boundary_topic_deflection
test_pillar_hierarchy_privacy_beats_factual    # Rule 13
```

### Invariants (tests/invariants/test_phase4.py)

```
test_blocked_message_never_delivered
test_identity_doc_never_modified_by_llm    # Rule 11
test_nonneg_cannot_be_disabled_at_runtime
test_redaction_bug_does_not_reveal_vault
test_T11_prompt_injection_does_not_circumvent_nonneg    # adversarial fixture
```

---

## Out of scope for this phase

- Actual MCP delivery (Phase 5).
- Group-conversation tone handling (Phase 5; groups are counterparties too).
- Multi-persona federation (explicit non-goal per CLAUDE.md §16).
- Live operator override UI for drift flags (CLI only in Phase 4; web UI in a later product release).
- Auto-rotation of identity documents (operator manually re-runs `identity load` with a new version).

---

## Common pitfalls

**Identity caching invalidation.** Identity documents are loaded from DB and cached in memory. If you `identity load` a new version, the cache must invalidate. The loader module publishes an invalidation event; subscribers refresh. If you see "old identity still in effect after update," the cache invalidation is broken.

**Non-negotiable evaluator false positives.** The LLM evaluator will over-block on ambiguous drafts. Expected. Measure block rate; if it's > 20% across representative drafts, the rules are too aggressive or the LLM needs a tighter prompt. Iterate on the prompt template, not on the rules.

**Redactor missing the active counterparty's own email.** The redactor strips emails, but the active counterparty's email is legitimate to mention back to them. The `allowed` set handles this; if a bug drops emails of the person you're talking to, check `allowed` population.

**Cross-counterparty name leak.** The redactor replaces `display_name` of other counterparties, but if you have two counterparties named "Alice," you can't distinguish. Phase 4 accepts this as a limitation; document it. Phase 6 or later may introduce per-counterparty nicknames with explicit aliasing.

**Tone profile over-analysis.** Running tone analysis on every message is expensive. The 10-message interval is a sweet spot. Don't lower; raise if LLM costs are still a concern.

**Identity document size.** Large identity docs (>10KB) slow every outbound approval because the full doc goes into the non-negotiable LLM context. Keep docs focused; use boundaries not-wishlists.

**Signature tampering on identity.** If an attacker with write access to the DB modifies `personas.identity_doc`, signature verification at load catches it — but only if you actually verify. Make sure `load_identity` re-verifies on every miss, not just once at startup.

---

## When Phase 4 closes

Tag: `git tag phase-4-complete`. Update `CLAUDE.md` §8.

Commit message: `feat(phase4): identity docs, non-negotiables, tone profiles, outbound approval`.
