"""Observability — metrics, structured logging, and health probes.

Phase 6 module. Provides:
  - metrics: Prometheus-compatible counter/gauge/histogram registry
  - logging: JSON-structured logger with required fields (ts, level, module, event)

See docs/blueprint/08_blocking_gaps_closure.md §1 for the full metric catalog
and runbook references.
"""
