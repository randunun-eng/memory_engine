"""Outbound approval pipeline.

Every outbound reply passes through this pipeline before delivery:
1. Non-negotiable check (hard block)
2. Forbidden topic check (hard block)
3. Self-contradiction check (block + drift flag)
4. Cross-counterparty redaction
5. PII redaction

The pipeline runs sequentially. A block at any step stops evaluation.
A redaction at steps 4-5 modifies the text but doesn't block.

This pipeline is an egress concern. It does not modify memory.
Identity document changes affect outbound evaluation going forward;
they do NOT retroactively modify existing neurons.

SECURITY NOTE — LLM judge exposure:
When a PolicyDispatch is provided, the nonneg_judge LLM call receives the
unredacted draft. This is necessary for accurate evaluation (redacted text
would break detection of PII-containing violations). If the LLM is local
(e.g. Ollama), the data stays on-host. If the LLM is a remote API, the
operator is sending counterparty PII to a third party for evaluation.
Operators should be aware of this trade-off and configure LLM routing
accordingly (see config/litellm.yaml).

INVARIANT — rule 13 (privacy > everything):
When a block occurs, the drift flag and the returned ApprovalResult must
contain PII-redacted text, not the original draft. The event log is
immutable; a PII leak in the audit trail stays forever. Evaluation sees
the original; persistence sees the redacted version.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from memory_engine.policy.dispatch import PolicyDispatch

from memory_engine.identity.drift import (
    check_forbidden_topics,
    check_self_fact_contradiction,
    flag_identity_drift,
)
from memory_engine.identity.persona import IdentityDocument, load_identity
from memory_engine.outbound.redactor import (
    redact_cross_counterparty,
    redact_pii,
)

logger = logging.getLogger(__name__)


class OutboundVerdict(Enum):
    """Result of the outbound approval pipeline."""

    APPROVED = "approved"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """Full result of outbound approval."""

    verdict: OutboundVerdict
    text: str              # approved: possibly-redacted text. blocked: original text.
    reason: str | None     # why it was blocked, or None if approved
    redactions: tuple[str, ...] = ()  # descriptions of any redactions applied


async def approve_outbound(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    counterparty_id: int,
    reply_candidate: str,
    dispatch: PolicyDispatch | None = None,
) -> ApprovalResult:
    """Run the full outbound approval pipeline.

    Args:
        conn: Database connection.
        persona_id: The persona sending the message.
        counterparty_id: The counterparty receiving the message.
        reply_candidate: The draft outbound text.
        dispatch: PolicyDispatch for LLM-based checks. If None, LLM checks
            are skipped (useful for testing the non-LLM pipeline).

    Returns:
        ApprovalResult with verdict, (possibly redacted) text, and reason.
    """
    identity = await load_identity(conn, persona_id)

    if identity is None:
        # No identity document — allow but warn
        logger.warning(
            "Persona %d has no identity document; outbound approval skipped",
            persona_id,
        )
        return ApprovalResult(
            verdict=OutboundVerdict.APPROVED,
            text=reply_candidate,
            reason=None,
        )

    # Step 1: Non-negotiable check (hard block)
    if identity.has_non_negotiables:
        nonneg_result = await _check_non_negotiables(
            conn,
            persona_id=persona_id,
            identity=identity,
            text=reply_candidate,
            dispatch=dispatch,
        )
        if nonneg_result is not None:
            return nonneg_result

    # Step 2: Forbidden topic check (hard block)
    if identity.has_forbidden_topics:
        topic = check_forbidden_topics(reply_candidate, identity)
        if topic is not None:
            # Rule 13: redact PII before persisting to audit trail.
            # Evaluation saw unredacted text; persistence sees redacted.
            safe_text = redact_pii(reply_candidate).redacted
            await flag_identity_drift(
                conn,
                persona_id=persona_id,
                flag_type="forbidden_topic",
                candidate_text=safe_text,
                rule_text=topic,
            )
            return ApprovalResult(
                verdict=OutboundVerdict.BLOCKED,
                text=safe_text,
                reason=f"Forbidden topic: {topic}",
            )

    # Step 3: Self-contradiction check (block + drift flag)
    contradiction = check_self_fact_contradiction(reply_candidate, identity)
    if contradiction is not None:
        # Rule 13: redact PII before persisting to audit trail.
        safe_text = redact_pii(reply_candidate).redacted
        await flag_identity_drift(
            conn,
            persona_id=persona_id,
            flag_type="value_contradiction",
            candidate_text=safe_text,
            rule_text=contradiction,
        )
        return ApprovalResult(
            verdict=OutboundVerdict.BLOCKED,
            text=safe_text,
            reason=f"Self-contradiction: contradicts self_fact '{contradiction[:60]}...'",
        )

    # Step 4: Cross-counterparty redaction
    all_redactions: list[str] = []
    text = reply_candidate

    cp_result = await redact_cross_counterparty(
        conn,
        text,
        persona_id=persona_id,
        active_counterparty_id=counterparty_id,
    )
    if cp_result.was_redacted:
        text = cp_result.redacted
        all_redactions.extend(cp_result.redactions)

    # Step 5: PII redaction
    pii_result = redact_pii(text)
    if pii_result.was_redacted:
        text = pii_result.redacted
        all_redactions.extend(pii_result.redactions)

    return ApprovalResult(
        verdict=OutboundVerdict.APPROVED,
        text=text,
        reason=None,
        redactions=tuple(all_redactions),
    )


async def _check_non_negotiables(
    conn: aiosqlite.Connection,
    *,
    persona_id: int,
    identity: IdentityDocument,
    text: str,
    dispatch: PolicyDispatch | None,
) -> ApprovalResult | None:
    """Check text against all non-negotiables.

    If a dispatch is provided, uses the nonneg_judge LLM for each rule.
    If no dispatch, falls back to keyword matching (less accurate but
    works without an LLM backend).

    Returns:
        ApprovalResult with BLOCKED verdict if a violation is found,
        or None if all non-negotiables pass.
    """
    for rule in identity.non_negotiables:
        violated = False

        if dispatch is not None:
            # LLM-based evaluation
            try:
                result = await dispatch.dispatch(
                    site="nonneg_judge",
                    params={
                        "draft_message": text,
                        "non_negotiable_rule": rule,
                    },
                    persona_id=persona_id,
                )
                # Expected response: {"violated": true/false, "reason": "..."}
                violated = result.get("violated", False)
            except Exception:
                logger.exception(
                    "nonneg_judge dispatch failed for rule: %s", rule[:60]
                )
                # On LLM failure, fall through to keyword check
                violated = _keyword_nonneg_check(text, rule)
        else:
            # Keyword fallback
            violated = _keyword_nonneg_check(text, rule)

        if violated:
            # Rule 13: redact PII before persisting to audit trail.
            # Evaluation saw unredacted text; persistence sees redacted.
            safe_text = redact_pii(text).redacted
            await flag_identity_drift(
                conn,
                persona_id=persona_id,
                flag_type="nonneg_violation",
                candidate_text=safe_text,
                rule_text=rule,
            )
            return ApprovalResult(
                verdict=OutboundVerdict.BLOCKED,
                text=safe_text,
                reason=f"Non-negotiable violated: {rule}",
            )

    return None


def _keyword_nonneg_check(text: str, rule: str) -> bool:
    """Simple keyword-based non-negotiable check.

    Extracts key terms from the rule and checks if the text mentions them
    in a way that suggests violation. This is a rough heuristic — the LLM
    judge is more accurate but requires a running LLM.

    The check errs on the side of caution: if in doubt, it flags.
    """
    text_lower = text.lower()
    rule_lower = rule.lower()

    # "I never disclose X" → check if text contains X
    if "never disclose" in rule_lower or "never share" in rule_lower:
        # Extract what shouldn't be disclosed
        for marker in ("never disclose ", "never share "):
            if marker in rule_lower:
                protected = rule_lower.split(marker, 1)[1].rstrip(".")
                # Check if key terms from the protected item appear in text
                terms = [t for t in protected.split() if len(t) > 3]
                matches = sum(1 for t in terms if t in text_lower)
                if matches >= 2:
                    return True

    # "I never discuss X" → check if text discusses X
    if "never discuss" in rule_lower:
        protected = rule_lower.split("never discuss ", 1)[1].rstrip(".")
        terms = [t for t in protected.split() if len(t) > 3]
        matches = sum(1 for t in terms if t in text_lower)
        if matches >= 2:
            return True

    # "I never agree to X without Y" → check if text agrees to X
    if "never agree" in rule_lower and "without" in rule_lower:
        action = rule_lower.split("never agree to ", 1)
        if len(action) > 1:
            action_text = action[1].split(" without")[0]
            terms = [t for t in action_text.split() if len(t) > 3]
            matches = sum(1 for t in terms if t in text_lower)
            if matches >= 2:
                return True

    return False
