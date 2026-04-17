"""Identity document loader and accessor.

An identity document is a signed YAML file that defines a persona's
non-negotiables, self-facts, forbidden topics, and deletion policy.
The LLM never modifies identity documents (rule 11). It can flag drift
via identity_drift_flags; only the human decides.

Identity changes affect outbound evaluation going forward. They do NOT
retroactively modify existing neurons. Memory is durable; what you say
from memory is filtered. Non-negotiables are an egress concern, not a
memory concern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import aiosqlite

from memory_engine.exceptions import ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SelfFact:
    """A fact about the persona defined in the identity document."""

    text: str
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class DeletionPolicy:
    """How deletion/forget requests are handled."""

    inbound: str = "ignore"    # "ignore" or "honor"
    outbound: str = "honor"    # "ignore" or "honor"


@dataclass(frozen=True, slots=True)
class IdentityDocument:
    """Parsed and validated identity document for a persona.

    Immutable once loaded. The only way to change it is for a human
    to update the YAML source and reload. Rule 11.
    """

    persona_slug: str
    version: int
    signed_by: str
    signed_at: str
    self_facts: tuple[SelfFact, ...]
    non_negotiables: tuple[str, ...]
    forbidden_topics: tuple[str, ...]
    deletion_policy: DeletionPolicy
    raw_yaml: str  # original source, for audit

    @property
    def has_non_negotiables(self) -> bool:
        return len(self.non_negotiables) > 0

    @property
    def has_forbidden_topics(self) -> bool:
        return len(self.forbidden_topics) > 0


def parse_identity_yaml(yaml_text: str) -> IdentityDocument:
    """Parse an identity document from YAML text.

    Validates required fields. Does NOT verify the signature — that's
    a Phase 5 concern requiring the operator's keypair.

    Args:
        yaml_text: Raw YAML string.

    Returns:
        Validated IdentityDocument.

    Raises:
        ConfigError: If required fields are missing or malformed.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ConfigError(f"Identity document is not valid YAML: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError("Identity document must be a YAML mapping")

    # Required fields
    for required in ("persona", "version", "signed_by", "signed_at"):
        if required not in data:
            raise ConfigError(f"Identity document missing required field: {required!r}")

    # Parse self_facts
    raw_facts = data.get("self_facts", [])
    if not isinstance(raw_facts, list):
        raise ConfigError("self_facts must be a list")
    self_facts = tuple(
        SelfFact(
            text=f["text"],
            confidence=f.get("confidence", 1.0),
        )
        for f in raw_facts
        if isinstance(f, dict) and "text" in f
    )

    # Parse non_negotiables
    raw_nonneg = data.get("non_negotiables", [])
    if not isinstance(raw_nonneg, list):
        raise ConfigError("non_negotiables must be a list")
    non_negotiables = tuple(str(nn) for nn in raw_nonneg)

    # Parse forbidden_topics
    raw_forbidden = data.get("forbidden_topics", [])
    if not isinstance(raw_forbidden, list):
        raise ConfigError("forbidden_topics must be a list")
    forbidden_topics = tuple(str(ft) for ft in raw_forbidden)

    # Parse deletion_policy
    raw_del = data.get("deletion_policy", {})
    if not isinstance(raw_del, dict):
        raise ConfigError("deletion_policy must be a mapping")
    deletion_policy = DeletionPolicy(
        inbound=str(raw_del.get("inbound", "ignore")),
        outbound=str(raw_del.get("outbound", "honor")),
    )

    return IdentityDocument(
        persona_slug=str(data["persona"]),
        version=int(data["version"]),
        signed_by=str(data["signed_by"]),
        signed_at=str(data["signed_at"]),
        self_facts=self_facts,
        non_negotiables=non_negotiables,
        forbidden_topics=forbidden_topics,
        deletion_policy=deletion_policy,
        raw_yaml=yaml_text,
    )


async def load_identity(
    conn: aiosqlite.Connection,
    persona_id: int,
) -> IdentityDocument | None:
    """Load a persona's identity document from the database.

    The identity YAML is stored in personas.identity_doc. If the column
    is NULL, the persona has no identity document (valid for dev/test).

    Returns:
        Parsed IdentityDocument, or None if no identity_doc is set.

    Raises:
        ConfigError: If the YAML is malformed.
    """
    cursor = await conn.execute(
        "SELECT identity_doc FROM personas WHERE id = ?",
        (persona_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    yaml_text = row["identity_doc"]
    if yaml_text is None:
        return None

    return parse_identity_yaml(yaml_text)


async def save_identity(
    conn: aiosqlite.Connection,
    persona_id: int,
    yaml_text: str,
) -> IdentityDocument:
    """Save an identity document to the database.

    Validates the YAML before writing. The caller is responsible for
    ensuring this is a human-initiated action (rule 11).

    Returns:
        The parsed and saved IdentityDocument.

    Raises:
        ConfigError: If the YAML is malformed.
    """
    doc = parse_identity_yaml(yaml_text)

    await conn.execute(
        "UPDATE personas SET identity_doc = ?, version = version + 1 WHERE id = ?",
        (yaml_text, persona_id),
    )
    await conn.commit()

    logger.info(
        "Identity document updated for persona %d (v%d, signed by %s)",
        persona_id,
        doc.version,
        doc.signed_by,
    )

    return doc
