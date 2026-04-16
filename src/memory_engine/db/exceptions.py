"""DB-layer exceptions."""

from memory_engine.exceptions import MemoryEngineError


class MigrationError(MemoryEngineError):
    """Migration failed or checksum mismatch."""


class UpdateForbiddenError(MemoryEngineError):
    """Attempted UPDATE on immutable table. Rule 1."""


class DeleteForbiddenError(MemoryEngineError):
    """Attempted DELETE on immutable table. Rule 1."""
