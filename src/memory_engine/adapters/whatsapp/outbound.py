"""Outbound message preparation for WhatsApp delivery.

The engine approves or rejects outbound messages; the MCP delivers them.
This module bridges the outbound approval pipeline (Phase 4) with the
WhatsApp adapter layer.

Critical separation (from blueprint §5):
  - wiki-v3 approves or rejects; the MCP sends.
  - wiki-v3 never holds WhatsApp credentials after unseal-on-start.
  - wiki-v3 never makes outbound network calls to Meta's infrastructure.
  - If the WhatsApp session is broken, approved messages queue in the MCP.

If approval fails: the proposed content is not stored as a persona_output
event. The rejection is logged with reason. The downstream caller decides
whether to regenerate, rephrase, or escalate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.core.events import append_event, compute_content_hash
from memory_engine.outbound.approval import ApprovalResult, OutboundVerdict, approve_outbound
from memory_engine.policy.signing import canonical_signing_message, sign

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutboundPrepared:
    """A message approved and prepared for WhatsApp delivery."""

    approval: ApprovalResult
    event_id: int | None    # persona_output event id if approved, None if blocked
    counterparty_id: int
    persona_id: int


async def prepare_outbound(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    counterparty_id: int,
    reply_text: str,
    private_key: bytes,
    public_key_b64: str,
) -> OutboundPrepared:
    """Run the approval pipeline and prepare for WhatsApp delivery.

    If approved, creates a persona_output event in the log.
    If blocked, returns the rejection without creating an event.

    Args:
        conn: Database connection.
        persona_id: The persona sending the message.
        counterparty_id: The counterparty receiving the message.
        reply_text: The proposed outbound text.
        private_key: Ed25519 private key for signing the output event.
        public_key_b64: Corresponding public key for verification.

    Returns:
        OutboundPrepared with approval result and event metadata.
    """
    # Run the approval pipeline
    approval = await approve_outbound(
        conn,
        persona_id=persona_id,
        counterparty_id=counterparty_id,
        reply_candidate=reply_text,
    )

    if approval.verdict == OutboundVerdict.BLOCKED:
        logger.warning(
            "Outbound blocked for persona=%d counterparty=%d: %s",
            persona_id,
            counterparty_id,
            approval.reason,
        )
        return OutboundPrepared(
            approval=approval,
            event_id=None,
            counterparty_id=counterparty_id,
            persona_id=persona_id,
        )

    # Approved — create persona_output event
    payload: dict[str, Any] = {
        "content": approval.text,
        "source": "whatsapp",
        "direction": "outbound",
    }
    if approval.redactions:
        payload["redactions_applied"] = list(approval.redactions)

    content_hash = compute_content_hash(payload)
    message = canonical_signing_message(persona_id, content_hash)
    sig = sign(private_key, message)

    event = await append_event(
        conn,
        persona_id=persona_id,
        counterparty_id=counterparty_id,
        event_type="message_out",
        scope="shared",
        payload=payload,
        signature=sig,
        public_key_b64=public_key_b64,
    )

    logger.info(
        "Outbound approved for persona=%d counterparty=%d event=%d",
        persona_id,
        counterparty_id,
        event.id,
    )

    return OutboundPrepared(
        approval=approval,
        event_id=event.id,
        counterparty_id=counterparty_id,
        persona_id=persona_id,
    )
