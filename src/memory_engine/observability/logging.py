"""Structured JSON logging with required fields.

Every log line is a JSON object with at minimum: ts, level, module, event.
Everything else is event-specific context. The `event` field is the primary
filter key — operators search by event name, not by free text.

Usage:
    from memory_engine.observability.logging import get_logger, configure_json_logging

    configure_json_logging()  # once at process start
    log = get_logger(__name__)

    log.info("grounding_verdict", verdict="accepted", neuron_hash="abc123")
    # Emits: {"ts":"...","level":"info","module":"...","event":"grounding_verdict","verdict":"accepted","neuron_hash":"abc123"}

Required fields are enforced by the logger wrapper. Calls that don't pass
an `event` name fall back to the message as event — but this is a lint
signal; prefer explicit event names.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON with required fields."""

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "ts": datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "module": record.name,
        }

        # Extract event name — either from extra dict or fall back to message
        extras = getattr(record, "_structured", None)
        if extras and isinstance(extras, dict):
            base["event"] = extras.get("event", record.getMessage())
            for k, v in extras.items():
                if k != "event":
                    base[k] = v
        else:
            base["event"] = record.getMessage()

        # Include exception info if present
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)

        return json.dumps(base, separators=(",", ":"), default=str, ensure_ascii=False)


class StructuredLogger:
    """Thin wrapper over logging.Logger that enforces structured fields."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(self, level: int, event: str, **kwargs: Any) -> None:
        self._logger.log(level, event, extra={"_structured": {"event": event, **kwargs}})

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, event, **kwargs)


def get_logger(name: str) -> StructuredLogger:
    """Return a structured logger for the given module name."""
    return StructuredLogger(logging.getLogger(name))


def configure_json_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Call once at process start. Idempotent — removes any existing handlers
    to prevent duplicate lines.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
