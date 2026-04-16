"""Data shapes for retrieval results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class Neuron:
    id: int
    persona_id: int
    counterparty_id: int | None
    kind: str
    content: str
    tier: str
    t_valid_start: datetime | None
    t_valid_end: datetime | None
    recorded_at: datetime
    distinct_source_count: int
    embedder_rev: str


@dataclass(frozen=True, slots=True)
class Citation:
    event_id: int
    recorded_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class RecallScores:
    bm25: float
    vector: float
    graph: float
    fused: float
    rank_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecallResult:
    neuron: Neuron
    citations: tuple[Citation, ...]
    scores: RecallScores
