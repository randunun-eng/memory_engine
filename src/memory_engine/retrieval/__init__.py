"""memory_engine.retrieval — hybrid recall with lens enforcement.

Public API:
    recall() — top-level retrieval function
    parse_lens() — lens string to SQL WHERE
    RecallResult, Neuron, Citation, RecallScores — result data shapes
"""

from memory_engine.retrieval.api import recall
from memory_engine.retrieval.lens import LensFilter, parse_lens
from memory_engine.retrieval.models import Citation, Neuron, RecallResult, RecallScores

__all__ = [
    "Citation",
    "LensFilter",
    "Neuron",
    "RecallResult",
    "RecallScores",
    "parse_lens",
    "recall",
]
