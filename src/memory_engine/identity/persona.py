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
from typing import TYPE_CHECKING, Any

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

    inbound: str = "ignore"  # "ignore" or "honor"
    outbound: str = "honor"  # "ignore" or "honor"


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


def _normalize_rich_to_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convert twincore-alpha 'rich' schema to the legacy simple schema
    this parser consumes. See DRIFT `identity-schema-mismatch-twincore-vs-phase4`.

    Rich shape:   persona_slug, schema_version, owner, issued_at,
                  role.{title, domain, responsibilities[]}, values[],
                  tone_defaults.{formality, length_preference, emoji},
                  non_negotiables: [{id, rule, evaluator, trigger_patterns}]
    Legacy shape: persona, version, signed_by, signed_at,
                  self_facts: [{text, confidence}],
                  non_negotiables: [str],
                  forbidden_topics: [str],
                  deletion_policy: {inbound, outbound}

    Conversion rules:
    - self_facts derived from `values[]` (each value string becomes a
      SelfFact) plus `role.title` if present — these are persona-level
      declarations the model should treat as self-truth.
    - non_negotiables: each dict's `rule` field becomes the string.
    - version: schema_version is a dotted string ("1.0"); take the major
      part as the integer version.
    """
    out: dict[str, Any] = dict(data)  # preserve unknown keys
    # persona / version mapping
    if "persona" not in out and "persona_slug" in data:
        out["persona"] = data["persona_slug"]
    if "version" not in out:
        sv = data.get("schema_version")
        if sv is not None:
            try:
                out["version"] = int(str(sv).split(".")[0])
            except (ValueError, AttributeError):
                out["version"] = 1
    if "signed_by" not in out and "owner" in data:
        out["signed_by"] = data["owner"]
    if "signed_at" not in out and "issued_at" in data:
        out["signed_at"] = data["issued_at"]

    # Derive self_facts from role.title + values[] if not already provided.
    if "self_facts" not in out:
        self_facts: list[dict[str, Any]] = []
        role = data.get("role")
        if isinstance(role, dict) and isinstance(role.get("title"), str):
            self_facts.append({"text": f"I am {role['title']}.", "confidence": 1.0})
        values = data.get("values")
        if isinstance(values, list):
            for v in values:
                if isinstance(v, str) and v.strip():
                    self_facts.append({"text": v, "confidence": 1.0})
        if self_facts:
            out["self_facts"] = self_facts

    # Convert structured non_negotiables to flat strings.
    raw_nn = data.get("non_negotiables")
    if isinstance(raw_nn, list) and raw_nn and isinstance(raw_nn[0], dict):
        out["non_negotiables"] = [
            str(item.get("rule", "")).strip()
            for item in raw_nn
            if isinstance(item, dict) and item.get("rule")
        ]

    return out


def parse_identity_yaml(yaml_text: str) -> IdentityDocument:
    """Parse an identity document from YAML text.

    Accepts two schemas:
      - Legacy (Phase 4): persona, version, signed_by, signed_at, ...
      - Rich (twincore-alpha): persona_slug, schema_version, owner, issued_at,
        role{}, values[], tone_defaults{}, structured non_negotiables[].

    Rich docs are normalized to the legacy shape before validation. See
    DRIFT `identity-schema-mismatch-twincore-vs-phase4`. Validates required
    fields. Does NOT verify the signature — that's a Phase 5 concern.

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

    # Auto-detect rich schema by the presence of any rich-only key, and
    # normalize to the legacy shape so the downstream validator is happy.
    rich_markers = {"persona_slug", "schema_version", "owner", "issued_at", "role"}
    if rich_markers.intersection(data.keys()):
        data = _normalize_rich_to_legacy(data)

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
