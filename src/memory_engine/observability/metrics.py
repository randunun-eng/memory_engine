"""Prometheus-compatible metrics registry.

A minimal in-process metric registry that exposes counters, gauges, and
histograms in Prometheus text exposition format. No external dependency
(avoids prometheus_client to keep the surface area small).

Usage:
    from memory_engine.observability.metrics import (
        counter, gauge, histogram, render,
    )

    counter("wiki_v3_events_appended_total", labels={"persona": "sales", "type": "message_in"}).inc()
    gauge("wiki_v3_quarantine_depth", labels={"persona": "sales"}).set(42)
    histogram("wiki_v3_ingest_latency_seconds").observe(0.05)

    # GET /metrics handler:
    return render()

Metric catalog (from docs/blueprint/08_blocking_gaps_closure.md §1.3):
    wiki_v3_events_appended_total         counter
    wiki_v3_events_rejected_total         counter
    wiki_v3_ingest_latency_seconds        histogram
    wiki_v3_grounding_gate_verdict_total  counter
    wiki_v3_quarantine_depth              gauge
    wiki_v3_neurons_total                 gauge
    wiki_v3_distinct_source_ratio         gauge
    wiki_v3_recall_latency_seconds        histogram
    wiki_v3_recall_degraded_total         counter
    wiki_v3_invariant_check_total         counter
    wiki_v3_invariant_violation_total     counter
    wiki_v3_persona_output_verdict_total  counter
    wiki_v3_identity_flag_total           counter
    wiki_v3_llm_cost_usd_total            counter
    wiki_v3_llm_cache_hit_ratio           gauge
    wiki_v3_event_log_size_bytes          gauge
    wiki_v3_mcp_auth_failures_total       counter
    wiki_v3_consolidator_lag_seconds      gauge
    wiki_v3_backup_last_success_seconds   gauge
    wiki_v3_backup_size_bytes             gauge
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal

MetricKind = Literal["counter", "gauge", "histogram"]

# Default histogram buckets (seconds) — covers sub-ms to 10s.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _label_key(labels: dict[str, str] | None) -> str:
    """Canonical key for a label set. Empty → ''."""
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _escape(value: str) -> str:
    """Escape a label value for Prometheus text format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labels: dict[str, str] | None) -> str:
    """Render labels as Prometheus-format {k="v",k2="v2"}."""
    if not labels:
        return ""
    parts = [f'{k}="{_escape(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


@dataclass
class _CounterSeries:
    value: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            msg = "Counter cannot be decremented"
            raise ValueError(msg)
        self.value += amount


@dataclass
class _GaugeSeries:
    value: float = 0.0

    def set(self, value: float) -> None:
        self.value = value

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        self.value -= amount


@dataclass
class _HistogramSeries:
    buckets: tuple[float, ...]
    bucket_counts: list[int] = field(default_factory=list)
    sum: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        if not self.bucket_counts:
            self.bucket_counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.sum += value
        self.count += 1
        for i, le in enumerate(self.buckets):
            if value <= le:
                self.bucket_counts[i] += 1


@dataclass
class _Metric:
    name: str
    kind: MetricKind
    help_text: str
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    # label_key → series
    series: dict[str, _CounterSeries | _GaugeSeries | _HistogramSeries] = field(
        default_factory=dict,
    )
    # label_key → labels (preserved for rendering)
    label_sets: dict[str, dict[str, str]] = field(default_factory=dict)


class Registry:
    """Thread-safe metric registry. Singleton via module-level `_REGISTRY`."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def counter(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        *,
        help_text: str = "",
    ) -> _CounterSeries:
        return self._get_or_create(name, "counter", labels, help_text=help_text)  # type: ignore[return-value]

    def gauge(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        *,
        help_text: str = "",
    ) -> _GaugeSeries:
        return self._get_or_create(name, "gauge", labels, help_text=help_text)  # type: ignore[return-value]

    def histogram(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        *,
        help_text: str = "",
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> _HistogramSeries:
        return self._get_or_create(  # type: ignore[return-value]
            name, "histogram", labels, help_text=help_text, buckets=buckets,
        )

    def _get_or_create(
        self,
        name: str,
        kind: MetricKind,
        labels: dict[str, str] | None,
        *,
        help_text: str,
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> _CounterSeries | _GaugeSeries | _HistogramSeries:
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = _Metric(
                    name=name, kind=kind, help_text=help_text, buckets=buckets,
                )
            metric = self._metrics[name]
            if metric.kind != kind:
                msg = (
                    f"Metric {name!r} already registered as {metric.kind}, "
                    f"cannot re-register as {kind}"
                )
                raise ValueError(msg)

            key = _label_key(labels)
            if key not in metric.series:
                if kind == "counter":
                    metric.series[key] = _CounterSeries()
                elif kind == "gauge":
                    metric.series[key] = _GaugeSeries()
                else:  # histogram
                    metric.series[key] = _HistogramSeries(buckets=metric.buckets)
                metric.label_sets[key] = dict(labels) if labels else {}

            return metric.series[key]

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            for metric in self._metrics.values():
                if metric.help_text:
                    lines.append(f"# HELP {metric.name} {metric.help_text}")
                lines.append(f"# TYPE {metric.name} {metric.kind}")

                for key, series in metric.series.items():
                    labels = metric.label_sets.get(key, {})
                    label_str = _render_labels(labels)

                    if isinstance(series, _CounterSeries | _GaugeSeries):
                        lines.append(f"{metric.name}{label_str} {series.value}")
                    else:  # histogram
                        for le, count in zip(series.buckets, series.bucket_counts, strict=False):
                            bucket_labels = dict(labels)
                            bucket_labels["le"] = str(le)
                            lines.append(
                                f"{metric.name}_bucket{_render_labels(bucket_labels)} {count}"
                            )
                        # +Inf bucket
                        inf_labels = dict(labels)
                        inf_labels["le"] = "+Inf"
                        lines.append(
                            f"{metric.name}_bucket{_render_labels(inf_labels)} {series.count}"
                        )
                        lines.append(f"{metric.name}_sum{label_str} {series.sum}")
                        lines.append(f"{metric.name}_count{label_str} {series.count}")

        return "\n".join(lines) + "\n"

    def clear(self) -> None:
        """Reset all metrics. Primarily for testing."""
        with self._lock:
            self._metrics.clear()


# Module-level singleton
_REGISTRY = Registry()


def counter(
    name: str,
    labels: dict[str, str] | None = None,
    *,
    help_text: str = "",
) -> _CounterSeries:
    """Get or create a counter series."""
    return _REGISTRY.counter(name, labels, help_text=help_text)


def gauge(
    name: str,
    labels: dict[str, str] | None = None,
    *,
    help_text: str = "",
) -> _GaugeSeries:
    """Get or create a gauge series."""
    return _REGISTRY.gauge(name, labels, help_text=help_text)


def histogram(
    name: str,
    labels: dict[str, str] | None = None,
    *,
    help_text: str = "",
    buckets: tuple[float, ...] = DEFAULT_BUCKETS,
) -> _HistogramSeries:
    """Get or create a histogram series."""
    return _REGISTRY.histogram(name, labels, help_text=help_text, buckets=buckets)


def render() -> str:
    """Render all registered metrics in Prometheus text format."""
    return _REGISTRY.render()


def clear() -> None:
    """Reset the registry. Primarily for testing."""
    _REGISTRY.clear()
