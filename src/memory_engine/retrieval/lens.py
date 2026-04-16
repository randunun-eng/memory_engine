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
      - 'auto'               -> defaults to 'self' in Phase 1

    The returned WHERE clause is AND-appended to whatever outer query runs.
    Always scoped to persona_id.

    Raises:
        ValueError: Unknown lens format.
    """
    if lens == "auto":
        return parse_lens("self", persona_id)

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
