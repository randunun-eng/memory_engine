"""Lens parsing and SQL WHERE generation.

The lens parameter restricts retrieval to a scope. This is the critical
boundary for rule 12 — cross-counterparty retrieval is structurally
forbidden in the normal API.

Every retrieval stream applies the lens filter. No bypass path exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LensFilter:
    """A parameterized SQL WHERE clause for lens enforcement."""

    where_clause: str
    params: tuple[int | str, ...]


def parse_lens(lens: str, persona_id: int) -> LensFilter:
    """Translate a lens string into a SQL WHERE filter.

    Lens grammar:
      - 'self'               -> self_facts only
      - 'counterparty:<ref>' -> counterparty_facts for <ref> + domain_facts
      - 'domain'             -> domain_facts only
      - 'auto'               -> everything the persona can see (all kinds,
                                all counterparties). Rule 12 still holds:
                                results never leave the persona.

    Phase 1 pinned 'auto' to 'self' as a stub; Phase 7 operator use
    requires the twin-agent to recall across any contact without having
    to pass an explicit counterparty — otherwise counterparty_fact-heavy
    deployments see empty results. Cross-counterparty LEAKAGE is still
    structurally prevented by Rule 12 at the admin-path level (the
    `admin_cross_counterparty_recall` function is the only cross-persona
    surface); this lens stays inside a single persona.

    The returned WHERE clause is AND-appended to whatever outer query runs.
    Always scoped to persona_id.

    Raises:
        ValueError: Unknown lens format.
    """
    if lens == "auto":
        return LensFilter(
            where_clause="(n.persona_id = ?)",
            params=(persona_id,),
        )

    if lens == "self":
        return LensFilter(
            where_clause="(n.persona_id = ? AND n.kind = 'self_fact')",
            params=(persona_id,),
        )

    if lens == "domain":
        return LensFilter(
            where_clause="(n.persona_id = ? AND n.kind = 'domain_fact')",
            params=(persona_id,),
        )

    if lens.startswith("counterparty:"):
        external_ref = lens.split(":", 1)[1]
        return LensFilter(
            where_clause=(
                "(n.persona_id = ? AND ("
                "(n.kind = 'counterparty_fact' AND n.counterparty_id = "
                "(SELECT id FROM counterparties WHERE persona_id = ? AND external_ref = ?)"
                ") OR n.kind = 'domain_fact'"
                "))"
            ),
            params=(persona_id, persona_id, external_ref),
        )

    msg = f"Unknown lens: {lens!r}"
    raise ValueError(msg)
