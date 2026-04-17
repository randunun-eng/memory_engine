"""Phase 4 integration tests — identity, outbound approval, redaction.

Tests verify the full outbound pipeline: identity loading, non-negotiable
enforcement, forbidden topics, self-contradiction detection, PII redaction,
and cross-counterparty name stripping.

Architectural invariant under test: identity document changes affect outbound
evaluation going forward. They do NOT retroactively modify existing neurons.
Memory is durable; what you say from memory is filtered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.exceptions import ConfigError
from memory_engine.identity.persona import (
    load_identity,
    parse_identity_yaml,
    save_identity,
)
from memory_engine.outbound.approval import (
    OutboundVerdict,
    approve_outbound,
)
from memory_engine.outbound.redactor import (
    redact_cross_counterparty,
    redact_pii,
)
from tests.fixtures.personas import make_test_persona

# ---- Test identity YAML ----

_TEST_IDENTITY_YAML = """\
persona: test_twin
version: 1
signed_by: test@example.org
signed_at: 2026-04-16T10:00:00Z

self_facts:
  - text: "I am a digital assistant representing Randunu's consulting business."
    confidence: 1.0
  - text: "I am based in Colombo, Sri Lanka."
    confidence: 1.0

non_negotiables:
  - "I never disclose Randunu's personal email or phone number."
  - "I never agree to meeting times without checking Randunu's calendar first."
  - "I never discuss pricing without confirming the current rate card."

forbidden_topics:
  - politics
  - other_clients_by_name

deletion_policy:
  inbound: ignore
  outbound: honor
"""


# ---- Helpers ----


async def _setup_persona_with_identity(
    db: aiosqlite.Connection,
    yaml_text: str = _TEST_IDENTITY_YAML,
) -> tuple[int, str]:
    """Create a persona with an identity document. Returns (persona_id, public_key_b64)."""
    persona = await make_test_persona(db)
    await save_identity(db, persona.id, yaml_text)
    return persona.id, persona.public_key_b64


async def _setup_counterparty(
    db: aiosqlite.Connection,
    persona_id: int,
    *,
    external_ref: str = "whatsapp:+1234567890",
    display_name: str = "Alice",
) -> int:
    """Create a counterparty and return its id."""
    cursor = await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref, display_name) VALUES (?, ?, ?)",
        (persona_id, external_ref, display_name),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


# ---- Identity loading tests ----


async def test_identity_loads_from_yaml(db: aiosqlite.Connection) -> None:
    """parse_identity_yaml produces a valid IdentityDocument."""
    doc = parse_identity_yaml(_TEST_IDENTITY_YAML)
    assert doc.persona_slug == "test_twin"
    assert doc.version == 1
    assert doc.signed_by == "test@example.org"
    assert len(doc.non_negotiables) == 3
    assert len(doc.self_facts) == 2
    assert len(doc.forbidden_topics) == 2
    assert doc.deletion_policy.inbound == "ignore"
    assert doc.deletion_policy.outbound == "honor"


async def test_identity_loads_from_db(db: aiosqlite.Connection) -> None:
    """Identity saved to DB can be loaded back."""
    pid, _ = await _setup_persona_with_identity(db)
    doc = await load_identity(db, pid)
    assert doc is not None
    assert doc.persona_slug == "test_twin"
    assert len(doc.non_negotiables) == 3


async def test_identity_missing_returns_none(db: aiosqlite.Connection) -> None:
    """Persona without identity_doc returns None."""
    persona = await make_test_persona(db)
    doc = await load_identity(db, persona.id)
    assert doc is None


async def test_identity_invalid_yaml_raises(db: aiosqlite.Connection) -> None:
    """Malformed YAML raises ConfigError."""
    with pytest.raises(ConfigError, match="not valid YAML"):
        parse_identity_yaml("{{invalid: yaml:")


async def test_identity_missing_required_field_raises(db: aiosqlite.Connection) -> None:
    """YAML without required 'persona' field raises ConfigError."""
    with pytest.raises(ConfigError, match="missing required field"):
        parse_identity_yaml("version: 1\nsigned_by: x\nsigned_at: x")


# ---- Non-negotiable tests ----


async def test_non_negotiable_blocks_email_disclosure(db: aiosqlite.Connection) -> None:
    """Outbound mentioning personal email is blocked."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Sure, Randunu's personal email is randunu@private.com and his phone number is +94771234567.",
    )
    assert result.verdict == OutboundVerdict.BLOCKED
    assert "non_negotiable" in (result.reason or "").lower() or "Non-negotiable" in (result.reason or "")


async def test_non_negotiable_blocks_meeting_agreement(db: aiosqlite.Connection) -> None:
    """Outbound agreeing to meeting times without calendar check is blocked."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Yes, let's agree to meeting times for next Tuesday at 3pm. That works perfectly.",
    )
    assert result.verdict == OutboundVerdict.BLOCKED


async def test_non_negotiable_blocks_pricing_discussion(db: aiosqlite.Connection) -> None:
    """Outbound discussing pricing without rate card confirmation is blocked."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="The pricing for our consulting services is $200/hour. We never discuss pricing without checking, but here it is.",
    )
    assert result.verdict == OutboundVerdict.BLOCKED


async def test_non_negotiable_allows_safe_message(db: aiosqlite.Connection) -> None:
    """Benign message passes all non-negotiable checks."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Thank you for reaching out. I'd be happy to help with your project requirements.",
    )
    assert result.verdict == OutboundVerdict.APPROVED


# ---- Forbidden topic tests ----


async def test_forbidden_topic_blocks_politics(db: aiosqlite.Connection) -> None:
    """Outbound mentioning politics is blocked."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="I think the current politics situation is very concerning.",
    )
    assert result.verdict == OutboundVerdict.BLOCKED
    assert "Forbidden topic" in (result.reason or "")


async def test_forbidden_topic_blocks_client_names(db: aiosqlite.Connection) -> None:
    """Outbound mentioning other clients by name is blocked."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="We also work with other clients by name like Acme Corp.",
    )
    assert result.verdict == OutboundVerdict.BLOCKED


# ---- Self-contradiction tests ----


async def test_self_contradiction_blocks(db: aiosqlite.Connection) -> None:
    """Outbound contradicting a self_fact is blocked."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="I'm not based in Colombo, Sri Lanka. I operate from London.",
    )
    assert result.verdict == OutboundVerdict.BLOCKED
    assert "contradiction" in (result.reason or "").lower()


async def test_self_contradiction_flags_drift(db: aiosqlite.Connection) -> None:
    """Self-contradiction creates an identity_drift_flags entry."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="I'm not based in Colombo, Sri Lanka. I'm somewhere else.",
    )

    cursor = await db.execute(
        "SELECT * FROM identity_drift_flags WHERE persona_id = ? AND flag_type = 'value_contradiction'",
        (pid,),
    )
    row = await cursor.fetchone()
    assert row is not None


# ---- Redactor tests ----


async def test_redactor_strips_email(db: aiosqlite.Connection) -> None:
    """PII redactor strips email addresses."""
    result = redact_pii("Contact me at secret@example.com for details.")
    assert result.was_redacted
    assert "secret@example.com" not in result.redacted
    assert "[REDACTED]" in result.redacted


async def test_redactor_preserves_allowed_email(db: aiosqlite.Connection) -> None:
    """PII redactor preserves allowed counterparty email."""
    result = redact_pii(
        "Your email is alice@example.com, right?",
        allowed_emails=frozenset({"alice@example.com"}),
    )
    assert not result.was_redacted
    assert "alice@example.com" in result.redacted


async def test_redactor_strips_phone(db: aiosqlite.Connection) -> None:
    """PII redactor strips phone numbers."""
    result = redact_pii("Call me at +94 77 123 4567.")
    assert result.was_redacted
    assert "+94 77 123 4567" not in result.redacted


async def test_redactor_strips_ssn(db: aiosqlite.Connection) -> None:
    """PII redactor strips SSN-like patterns."""
    result = redact_pii("My SSN is 123-45-6789.")
    assert result.was_redacted
    assert "123-45-6789" not in result.redacted


async def test_redactor_strips_cross_counterparty_names(db: aiosqlite.Connection) -> None:
    """Cross-counterparty redactor strips other counterparties' names."""
    pid, _ = await _setup_persona_with_identity(db)
    alice_id = await _setup_counterparty(db, pid, external_ref="whatsapp:+1111", display_name="Alice")
    await _setup_counterparty(db, pid, external_ref="whatsapp:+2222", display_name="Bob Johnson")

    result = await redact_cross_counterparty(
        db,
        "I was talking to Bob Johnson about this project.",
        persona_id=pid,
        active_counterparty_id=alice_id,
    )
    assert result.was_redacted
    assert "Bob Johnson" not in result.redacted
    assert "[REDACTED]" in result.redacted


async def test_redactor_preserves_active_counterparty(db: aiosqlite.Connection) -> None:
    """Cross-counterparty redactor preserves the active counterparty's name."""
    pid, _ = await _setup_persona_with_identity(db)
    alice_id = await _setup_counterparty(db, pid, external_ref="whatsapp:+1111", display_name="Alice")
    await _setup_counterparty(db, pid, external_ref="whatsapp:+2222", display_name="Bob")

    result = await redact_cross_counterparty(
        db,
        "Alice, here's what I found for you.",
        persona_id=pid,
        active_counterparty_id=alice_id,
    )
    # Alice is the active counterparty — should NOT be redacted
    assert not result.was_redacted


# ---- Deletion policy tests ----


async def test_deletion_policy_inbound_ignore(db: aiosqlite.Connection) -> None:
    """Identity document with inbound='ignore' means we don't delete on request."""
    doc = parse_identity_yaml(_TEST_IDENTITY_YAML)
    assert doc.deletion_policy.inbound == "ignore"


async def test_deletion_policy_outbound_honor(db: aiosqlite.Connection) -> None:
    """Identity document with outbound='honor' means we stop replying on request."""
    doc = parse_identity_yaml(_TEST_IDENTITY_YAML)
    assert doc.deletion_policy.outbound == "honor"


# ---- Pipeline integration tests ----


async def test_pipeline_redacts_pii_in_approved_message(db: aiosqlite.Connection) -> None:
    """Full pipeline: approved message gets PII redacted."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Here's a contact: secret@private.com. Hope that helps!",
    )
    assert result.verdict == OutboundVerdict.APPROVED
    assert "secret@private.com" not in result.text
    assert "[REDACTED]" in result.text
    assert len(result.redactions) > 0


async def test_pipeline_no_identity_allows_through(db: aiosqlite.Connection) -> None:
    """Persona with no identity doc: outbound approved (with warning)."""
    persona = await make_test_persona(db)
    cpid = await _setup_counterparty(db, persona.id)

    result = await approve_outbound(
        db,
        persona_id=persona.id,
        counterparty_id=cpid,
        reply_candidate="Any message goes through without identity doc.",
    )
    assert result.verdict == OutboundVerdict.APPROVED


async def test_nonneg_drift_flag_created_on_block(db: aiosqlite.Connection) -> None:
    """Non-negotiable violation creates a drift flag."""
    pid, _ = await _setup_persona_with_identity(db)
    cpid = await _setup_counterparty(db, pid)

    await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Randunu's personal email is test@private.com and phone number is +1234567890.",
    )

    cursor = await db.execute(
        "SELECT * FROM identity_drift_flags WHERE persona_id = ? AND flag_type = 'nonneg_violation'",
        (pid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["reviewed_at"] is None  # pending review
