"""Exception hierarchy for memory_engine.

Every error raised by library code inherits from MemoryEngineError. Application
code can catch MemoryEngineError to handle our error domain; should never
catch Exception at function boundaries.
"""


class MemoryEngineError(Exception):
    """Root of all memory_engine exceptions. Never raise this directly."""


class ConfigError(MemoryEngineError):
    """Configuration problem at load or startup."""


class SignatureInvalid(MemoryEngineError):
    """Signature verification failed."""


class IdempotencyConflict(MemoryEngineError):
    """Event with this idempotency key already exists."""


class InvariantViolation(MemoryEngineError):
    """A governance invariant was violated.

    Subclasses indicate severity. Critical violations halt the system.
    """


class ScopeViolation(InvariantViolation):
    """Scope mismatch detected. Always critical."""


class CrossCounterpartyLeak(InvariantViolation):
    """Cross-counterparty data exposure detected. Always critical."""


# ---- Policy plane errors ----


class PromptNotFound(MemoryEngineError):
    """No active prompt template for the requested site."""


class DispatchError(MemoryEngineError):
    """LLM call failed during dispatch."""


class LLMResponseParseError(MemoryEngineError):
    """LLM response could not be parsed into expected structure."""


# ---- Grounding errors ----


class GroundingRejection(MemoryEngineError):
    """Candidate neuron rejected by the grounding gate."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"Grounding rejected: {reason}" + (f" ({detail})" if detail else ""))


# ---- Outbound errors ----


class OutboundBlocked(MemoryEngineError):
    """Outbound message blocked by approval pipeline."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Outbound blocked: {reason}")
