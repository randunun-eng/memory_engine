"""Egress redactor — strips PII and cross-counterparty references.

Before any outbound reply is delivered, the redactor:
1. Strips email addresses, phone numbers, SSN-like patterns that don't
   belong to the active counterparty.
2. Checks for other counterparties' names or identifiers.
3. Replaces matched patterns with [REDACTED].

Every redaction is logged. The redactor never blocks — it transforms.
Blocking is the approval pipeline's job.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Result of running the redactor on a text."""

    original: str
    redacted: str
    redactions: tuple[str, ...]  # descriptions of what was redacted

    @property
    def was_redacted(self) -> bool:
        return self.original != self.redacted


# ---- Pattern definitions ----

# Email addresses
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)

# Phone numbers (international formats)
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"
)

# SSN-like patterns (US format)
_SSN_RE = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"
)

# API keys / tokens (common patterns)
_SECRET_RE = re.compile(
    r"\b(?:sk-[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9._-]{20,})\b"
)


def redact_pii(
    text: str,
    *,
    allowed_emails: frozenset[str] | None = None,
    allowed_phones: frozenset[str] | None = None,
) -> RedactionResult:
    """Strip PII patterns from text.

    Allowed patterns (belonging to the active counterparty) are preserved.
    Everything else matching PII patterns is replaced with [REDACTED].

    Args:
        text: The text to redact.
        allowed_emails: Email addresses that belong to the active counterparty.
        allowed_phones: Phone numbers that belong to the active counterparty.

    Returns:
        RedactionResult with original, redacted text, and descriptions.
    """
    allowed_emails = allowed_emails or frozenset()
    allowed_phones = allowed_phones or frozenset()

    redactions: list[str] = []
    result = text

    # Redact SSNs (always — never allowed)
    ssn_matches = _SSN_RE.findall(result)
    for match in ssn_matches:
        result = result.replace(match, "[REDACTED]")
        redactions.append(f"SSN-like pattern: {match[:6]}...")

    # Redact secrets (always)
    secret_matches = _SECRET_RE.findall(result)
    for match in secret_matches:
        result = result.replace(match, "[REDACTED]")
        redactions.append(f"Secret/token pattern: {match[:10]}...")

    # Redact emails not in allowed set
    for match in _EMAIL_RE.finditer(result):
        email = match.group()
        if email.lower() not in {e.lower() for e in allowed_emails}:
            result = result.replace(email, "[REDACTED]")
            redactions.append(f"Email: {email}")

    # Redact phone numbers not in allowed set
    for match in _PHONE_RE.finditer(result):
        phone = match.group().strip()
        # Normalize: strip spaces and dashes for comparison
        phone_normalized = re.sub(r"[-.\s()]", "", phone)
        allowed_normalized = {re.sub(r"[-.\s()]", "", p) for p in allowed_phones}
        if phone_normalized not in allowed_normalized and len(phone_normalized) >= 7:
            result = result.replace(phone, "[REDACTED]")
            redactions.append(f"Phone: {phone}")

    return RedactionResult(
        original=text,
        redacted=result,
        redactions=tuple(redactions),
    )


async def redact_cross_counterparty(
    conn: aiosqlite.Connection,
    text: str,
    *,
    persona_id: int,
    active_counterparty_id: int,
) -> RedactionResult:
    """Strip references to other counterparties' names.

    Queries the counterparties table for all counterparties of this
    persona, then checks if any non-active counterparty's display_name
    appears in the text.

    Args:
        conn: Database connection.
        text: Text to check.
        persona_id: The persona.
        active_counterparty_id: The counterparty this message is for.

    Returns:
        RedactionResult with cross-counterparty names replaced.
    """
    cursor = await conn.execute(
        """
        SELECT id, display_name, external_ref FROM counterparties
        WHERE persona_id = ? AND id != ? AND display_name IS NOT NULL
        """,
        (persona_id, active_counterparty_id),
    )
    rows = await cursor.fetchall()

    redactions: list[str] = []
    result = text

    for row in rows:
        name = row["display_name"]
        if name and len(name) >= 2 and name.lower() in result.lower():
            # Case-insensitive replacement
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            result = pattern.sub("[REDACTED]", result)
            redactions.append(f"Cross-counterparty name: {name}")

    return RedactionResult(
        original=text,
        redacted=result,
        redactions=tuple(redactions),
    )
