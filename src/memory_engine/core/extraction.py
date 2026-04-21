"""LLM-driven entity extraction from events.

Produces NeuronCandidate objects from raw event payloads. All LLM calls go
through dispatch (policy plane invariant). Candidates are not neurons yet —
they must pass the grounding gate before promotion.

Rule 16: t_valid_start is never fabricated. If the extractor doesn't produce
one, it stays None.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from memory_engine.exceptions import LLMResponseParseError

if TYPE_CHECKING:
    from memory_engine.core.events import Event
    from memory_engine.policy.dispatch import PolicyDispatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NeuronCandidate:
    """A candidate neuron produced by extraction. Not yet grounded."""

    content: str
    confidence: float
    source_event_ids: list[int]
    t_valid_start: str | None  # ISO 8601 or None — rule 16: never fabricated
    source_span: str | None  # verbatim quote from source
    kind: str = "self_fact"  # inferred from event context
    target_tier: str = "working"  # initial tier before promotion


@dataclass
class ExtractionResult:
    """Result of extracting candidates from events."""

    candidates: list[NeuronCandidate] = field(default_factory=list)
    raw_response: str = ""


async def extract_candidates(
    dispatch: PolicyDispatch,
    *,
    events: list[Event],
    persona_id: int,
    counterparty_id: int | None = None,
) -> ExtractionResult:
    """Extract neuron candidates from a batch of events.

    Uses the extract_entities prompt site via dispatch. Returns candidates
    that still need to pass the grounding gate.

    Args:
        dispatch: The policy dispatch instance (every LLM call goes through here).
        events: Source events to extract from.
        persona_id: The persona these candidates belong to.
        counterparty_id: If set, candidates are counterparty_facts.

    Returns:
        ExtractionResult with candidates and raw LLM response.
    """
    if not events:
        return ExtractionResult()

    # Build the event content for the prompt
    event_content = _format_events_for_extraction(events)
    source_event_ids = [e.id for e in events]

    response = await dispatch.dispatch(
        "extract_entities",
        persona_id=persona_id,
        params={
            "event_content": event_content,
            "source_event_ids": source_event_ids,
        },
    )

    candidates = _parse_extraction_response(
        response,
        source_event_ids=source_event_ids,
        counterparty_id=counterparty_id,
    )

    return ExtractionResult(candidates=candidates, raw_response=json.dumps(response))


def _format_events_for_extraction(events: list[Event]) -> str:
    """Format events into a text block for the extraction prompt."""
    parts = []
    for event in events:
        payload = event.payload
        # Extract the message text from common payload shapes
        text = payload.get("text", payload.get("body", payload.get("content", "")))
        if not text and isinstance(payload, dict):
            text = json.dumps(payload)
        parts.append(f"[Event {event.id}] {text}")
    return "\n".join(parts)


def _parse_extraction_response(
    response: dict[str, Any],
    source_event_ids: list[int],
    counterparty_id: int | None,
) -> list[NeuronCandidate]:
    """Parse the extract_entities response into NeuronCandidate objects."""
    claims = response.get("claims", [])
    if not isinstance(claims, list):
        raise LLMResponseParseError(f"Expected 'claims' to be a list, got {type(claims).__name__}")

    # Determine kind from context
    kind = "counterparty_fact" if counterparty_id is not None else "self_fact"

    candidates = []
    for claim in claims:
        if not isinstance(claim, dict):
            logger.warning("Skipping non-dict claim: %s", claim)
            continue

        text = claim.get("text", "")
        if not text or not isinstance(text, str):
            continue

        confidence = float(claim.get("confidence", 0.0))
        if confidence < 0.4:
            # Below extraction threshold (per prompt spec)
            continue

        # Rule 16: only use t_valid_start if the extractor explicitly produced it
        t_valid_start = claim.get("t_valid_start")
        if t_valid_start is not None and not isinstance(t_valid_start, str):
            t_valid_start = None

        candidates.append(
            NeuronCandidate(
                content=text,
                confidence=confidence,
                source_event_ids=source_event_ids,
                t_valid_start=t_valid_start,
                source_span=claim.get("source_span"),
                kind=kind,
                target_tier="working",
            )
        )

    return candidates
