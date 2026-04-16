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
