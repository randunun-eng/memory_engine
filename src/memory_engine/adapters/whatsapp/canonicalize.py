"""Phone number and group JID canonicalization.

Every external reference must be normalized before counterparty lookup.
Without this, "+94 77 123 4567" and "+94771234567" become two different
counterparties — a data quality bug that silently fragments memory.

Canonical forms:
  1:1  → "whatsapp:+<E.164>"          e.g. "whatsapp:+94771234567"
  Group → "whatsapp-group:<jid>"       e.g. "whatsapp-group:1234567890-1699999999@g.us"
"""

from __future__ import annotations

import re

from memory_engine.exceptions import ConfigError


def canonicalize_phone(raw: str) -> str:
    """Normalize a phone number to canonical whatsapp:+E.164 format.

    Strips spaces, dashes, parentheses, dots. Ensures leading '+'.
    The result is the external_ref for a 1:1 counterparty.

    Args:
        raw: Raw phone number string. May include country code prefix,
            formatting characters, or the "whatsapp:" prefix.

    Returns:
        Canonical "whatsapp:+<digits>" string.

    Raises:
        ConfigError: If the result doesn't look like a valid phone number.
    """
    # Strip the whatsapp: prefix if already present
    number = raw.strip()
    if number.lower().startswith("whatsapp:"):
        number = number[len("whatsapp:") :]

    # Remove formatting characters
    number = re.sub(r"[\s\-\.\(\)]", "", number)

    # Ensure leading +
    if not number.startswith("+"):
        number = f"+{number}"

    # Validate: must be + followed by 7-15 digits (E.164 spec)
    if not re.fullmatch(r"\+\d{7,15}", number):
        raise ConfigError(
            f"Cannot canonicalize phone number: {raw!r} → {number!r} (expected +<7-15 digits>)"
        )

    return f"whatsapp:{number}"


def canonicalize_group_jid(raw: str) -> str:
    """Normalize a WhatsApp group JID to canonical form.

    WhatsApp group JIDs look like "1234567890-1699999999@g.us".
    We prefix with "whatsapp-group:" and strip any existing prefix.

    Args:
        raw: Raw group JID string.

    Returns:
        Canonical "whatsapp-group:<jid>" string.

    Raises:
        ConfigError: If the JID doesn't match expected group format.
    """
    jid = raw.strip()
    if jid.lower().startswith("whatsapp-group:"):
        jid = jid[len("whatsapp-group:") :]

    # Basic validation: should end with @g.us for groups
    if not jid.endswith("@g.us"):
        raise ConfigError(f"Cannot canonicalize group JID: {raw!r} (expected format: <id>@g.us)")

    return f"whatsapp-group:{jid}"


def is_group_ref(external_ref: str) -> bool:
    """Check if an external_ref is a group reference."""
    return external_ref.startswith("whatsapp-group:")
