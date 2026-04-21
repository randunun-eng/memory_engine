"""Phase 4 invariant tests — identity document immutability and pillar hierarchy.

Rule 11: Identity documents are authoritative, not derived. The LLM can read
and flag; only the human can change.

Rule 13: Pillar conflict hierarchy. When pillars disagree, the order is:
privacy > counterparty > persona > factual.

These tests verify structural properties of the identity and outbound system,
not functional behavior (that's in integration/test_phase4.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.identity.drift import flag_identity_drift
from memory_engine.identity.persona import load_identity, save_identity
from memory_engine.outbound.approval import (
    OutboundVerdict,
    approve_outbound,
)
from tests.fixtures.personas import make_test_persona

_TEST_IDENTITY_YAML = """\
persona: invariant_test
version: 1
signed_by: test@example.org
signed_at: 2026-04-16T10:00:00Z

self_facts:
  - text: "I represent a consulting business."
    confidence: 1.0

non_negotiables:
  - "I never disclose personal email or phone number."

forbidden_topics:
  - politics

deletion_policy:
  inbound: ignore
  outbound: honor
"""


# ---- Helpers ----


async def _make_persona_with_identity(
    db: aiosqlite.Connection,
) -> int:
    """Create a persona with identity doc, return persona_id."""
    persona = await make_test_persona(db)
    await save_identity(db, persona.id, _TEST_IDENTITY_YAML)
    return persona.id


async def _make_counterparty(
    db: aiosqlite.Connection,
    persona_id: int,
    *,
    external_ref: str = "whatsapp:+1234567890",
    display_name: str = "TestUser",
) -> int:
    cursor = await db.execute(
        "INSERT INTO counterparties (persona_id, external_ref, display_name) VALUES (?, ?, ?)",
        (persona_id, external_ref, display_name),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


# ---- Rule 11: Identity doc never modified by LLM ----


async def test_identity_doc_never_modified_by_llm(db: aiosqlite.Connection) -> None:
    """Rule 11: drift flags write to identity_drift_flags, not personas.identity_doc.

    Verifies that flagging drift does NOT change the identity document.
    The architectural invariant is: LLM flags drift, human decides.
    """
    pid = await _make_persona_with_identity(db)

    # Load identity before drift flag
    doc_before = await load_identity(db, pid)
    assert doc_before is not None

    # Simulate drift detection — flag it
    await flag_identity_drift(
        db,
        persona_id=pid,
        flag_type="value_contradiction",
        candidate_text="I am not a consulting business. I am a bakery.",
        rule_text="I represent a consulting business.",
    )

    # Identity doc must be unchanged
    doc_after = await load_identity(db, pid)
    assert doc_after is not None
    assert doc_after.raw_yaml == doc_before.raw_yaml
    assert doc_after.persona_slug == doc_before.persona_slug
    assert doc_after.non_negotiables == doc_before.non_negotiables
    assert doc_after.self_facts == doc_before.self_facts


async def test_drift_flag_does_not_create_identity_events(db: aiosqlite.Connection) -> None:
    """Rule 11: flagging drift never produces an event that modifies identity_doc.

    The drift flag table is separate from the event log's concern. Drift flags
    are metadata for human review, not state mutations.
    """
    pid = await _make_persona_with_identity(db)

    await flag_identity_drift(
        db,
        persona_id=pid,
        flag_type="nonneg_violation",
        candidate_text="Here's a secret email: x@y.com",
        rule_text="I never disclose personal email or phone number.",
    )

    # Drift flag must exist in the drift table
    cursor = await db.execute(
        "SELECT COUNT(*) FROM identity_drift_flags WHERE persona_id = ?",
        (pid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1

    # The personas table identity_doc column must be unchanged
    cursor2 = await db.execute(
        "SELECT identity_doc FROM personas WHERE id = ?",
        (pid,),
    )
    row2 = await cursor2.fetchone()
    assert row2 is not None
    assert row2["identity_doc"] == _TEST_IDENTITY_YAML


async def test_identity_doc_only_changes_via_save_identity(db: aiosqlite.Connection) -> None:
    """Rule 11: the only write path to personas.identity_doc is save_identity().

    Structural test: save_identity() is the gatekeeper. It validates YAML
    before writing. The approval pipeline and drift detection never call it.
    """
    pid = await _make_persona_with_identity(db)
    cpid = await _make_counterparty(db, pid)

    # Run the full outbound pipeline with a violation
    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="I never disclose personal email or phone number but here: x@y.com +1234567890",
    )
    assert result.verdict == OutboundVerdict.BLOCKED

    # Verify identity_doc is still exactly the original YAML
    cursor = await db.execute(
        "SELECT identity_doc FROM personas WHERE id = ?",
        (pid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["identity_doc"] == _TEST_IDENTITY_YAML


# ---- Rule 13: Pillar hierarchy — privacy > counterparty > persona > factual ----


async def test_pillar_hierarchy_privacy_first(db: aiosqlite.Connection) -> None:
    """Rule 13: privacy concerns override all other concerns.

    A message that would be approved by persona/factual rules is still
    blocked/redacted if it contains PII.
    """
    pid = await _make_persona_with_identity(db)
    cpid = await _make_counterparty(db, pid)

    # This message is benign from persona/factual perspective but has PII
    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Happy to help! Contact info: secret@private.com",
    )

    # Should be approved BUT with PII redacted (privacy wins)
    assert result.verdict == OutboundVerdict.APPROVED
    assert "secret@private.com" not in result.text
    assert "[REDACTED]" in result.text


async def test_pillar_hierarchy_nonneg_over_factual(db: aiosqlite.Connection) -> None:
    """Rule 13: persona non-negotiables override factual recall.

    Even if a fact is true and well-grounded, the non-negotiable blocks it
    from being spoken outbound.
    """
    pid = await _make_persona_with_identity(db)
    cpid = await _make_counterparty(db, pid)

    # A factually true statement that violates a non-negotiable
    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Randunu's personal email is real@actual.com and his phone number is +94771234567.",
    )

    # Non-negotiable blocks this even though it could be factually correct
    assert result.verdict == OutboundVerdict.BLOCKED


async def test_pillar_privacy_redaction_after_nonneg_pass(db: aiosqlite.Connection) -> None:
    """Rule 13: even after non-negotiable and forbidden checks pass,
    privacy redaction still applies.

    The pipeline is sequential: blocks first, then redaction. This tests
    that redaction isn't skipped when the message passes block checks.
    """
    pid = await _make_persona_with_identity(db)
    cpid = await _make_counterparty(db, pid)

    # Benign message that passes nonneg and forbidden checks, but has a phone
    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="I'll get back to you. My SSN is 123-45-6789 just kidding.",
    )

    assert result.verdict == OutboundVerdict.APPROVED
    assert "123-45-6789" not in result.text
    assert "[REDACTED]" in result.text


async def test_blocked_drift_flag_contains_redacted_pii(db: aiosqlite.Connection) -> None:
    """Rule 13: drift flags persisted on block must not contain raw PII.

    The event log is immutable — a PII leak in the audit trail stays forever.
    When the pipeline blocks a message, the drift flag's candidate_text must
    be the PII-redacted version, not the original draft.
    """
    pid = await _make_persona_with_identity(db)
    cpid = await _make_counterparty(db, pid)

    # This message violates a non-negotiable AND contains PII
    await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Randunu's personal email is secret@private.com and phone number is +94771234567.",
    )

    cursor = await db.execute(
        "SELECT candidate_text FROM identity_drift_flags WHERE persona_id = ? AND flag_type = 'nonneg_violation'",
        (pid,),
    )
    row = await cursor.fetchone()
    assert row is not None
    # PII must be redacted in the persisted drift flag
    assert "secret@private.com" not in row["candidate_text"]
    assert "[REDACTED]" in row["candidate_text"]


async def test_blocked_result_text_contains_redacted_pii(db: aiosqlite.Connection) -> None:
    """Rule 13: blocked ApprovalResult.text must not contain raw PII.

    The caller might log the result; PII must not leak through blocked results.
    """
    pid = await _make_persona_with_identity(db)
    cpid = await _make_counterparty(db, pid)

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=cpid,
        reply_candidate="Randunu's personal email is leak@example.com and phone number is +94771234567.",
    )

    assert result.verdict == OutboundVerdict.BLOCKED
    assert "leak@example.com" not in result.text
    assert "[REDACTED]" in result.text


async def test_cross_counterparty_redaction_enforces_privacy(db: aiosqlite.Connection) -> None:
    """Rule 13 + Rule 12: cross-counterparty names are redacted (privacy pillar).

    Even in a factually accurate and persona-approved message, mentioning
    another counterparty's name is a privacy violation that must be redacted.
    """
    pid = await _make_persona_with_identity(db)
    alice_id = await _make_counterparty(
        db, pid, external_ref="whatsapp:+1111", display_name="Alice"
    )
    await _make_counterparty(db, pid, external_ref="whatsapp:+2222", display_name="Bob Smith")

    result = await approve_outbound(
        db,
        persona_id=pid,
        counterparty_id=alice_id,
        reply_candidate="I was discussing your project with Bob Smith earlier.",
    )

    assert result.verdict == OutboundVerdict.APPROVED
    assert "Bob Smith" not in result.text
    assert "[REDACTED]" in result.text
