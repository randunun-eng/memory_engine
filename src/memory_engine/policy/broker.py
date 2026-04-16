"""Context broker for LLM prompts.

Declares what fields go into each prompt site. The broker is the single place
that decides which data a prompt template can see — it prevents accidental
leakage of fields into prompts that shouldn't have them.

Each site has a declared set of required and optional parameters. The broker
validates that required params are present before dispatch and strips
undeclared params to prevent prompt injection via unexpected fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from memory_engine.exceptions import DispatchError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SiteSpec:
    """Parameter specification for a prompt site."""

    site: str
    required: frozenset[str]
    optional: frozenset[str] = frozenset()


# Declared parameter contracts per site.
# If a site isn't listed here, the broker passes params through unfiltered
# (for forward compatibility with new sites added before the broker is updated).
_SITE_SPECS: dict[str, SiteSpec] = {}


def register_site(
    site: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    """Register a site's parameter contract."""
    _SITE_SPECS[site] = SiteSpec(
        site=site,
        required=frozenset(required),
        optional=frozenset(optional or set()),
    )


def validate_and_filter(site: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate params against the site spec and strip undeclared keys.

    Returns:
        Filtered params dict containing only declared parameters.

    Raises:
        DispatchError: If a required parameter is missing.
    """
    spec = _SITE_SPECS.get(site)
    if spec is None:
        # Unknown site — pass through (forward compat)
        return dict(params)

    # Check required
    missing = spec.required - params.keys()
    if missing:
        raise DispatchError(
            f"Missing required params for site={site!r}: {sorted(missing)}"
        )

    # Filter to declared params only
    allowed = spec.required | spec.optional
    filtered = {k: v for k, v in params.items() if k in allowed}

    stripped = set(params.keys()) - allowed
    if stripped:
        logger.debug("Stripped undeclared params for site=%s: %s", site, sorted(stripped))

    return filtered


# ---- Register Phase 2 sites ----

register_site(
    "extract_entities",
    required={"event_content", "source_event_ids"},
    optional={"existing_entities"},
)

register_site(
    "grounding_judge",
    required={"candidate_content", "source_events_text"},
)

register_site(
    "judge_contradiction",
    required={"neuron_a", "neuron_b", "entity_key"},
)

register_site(
    "summarize_episode",
    required={"events_text"},
    optional={"summary_max_words"},
)

register_site(
    "classify_scope",
    required={"message_content"},
)

register_site(
    "nonneg_judge",
    required={"draft_message", "non_negotiable_rule"},
)
