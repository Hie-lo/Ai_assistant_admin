"""Application metrics collection (Phase 10).

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1 section 3:
- job backlog/age
- sync duration/failure rate
- publish success/failure rate
- unknown remote state count
- duplicate-prevention conflicts
- API rate-limit events
- AI success/latency/cost
- database connections/latency
- CPU/RAM/disk
- backup success/restore-test status

This module provides an in-process metrics registry (no external dependency
required for V1). It can be exposed via /metrics (JSON) and optionally via
Prometheus format later. The implementation is deliberately simple to keep
resource usage low on the weak initial server.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class CounterMetric:
    name: str
    value: int = 0
    labels: dict[str, str] = field(default_factory=dict)


class MetricsRegistry:
    """Thread-safe in-memory metrics registry."""

    def __init__(self):
        self._lock = Lock()
        self._counters: Counter = Counter()
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._started_at = time.time()

    def inc(self, name: str, value: int = 1, labels: dict[str, str] | None = None):
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None):
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None):
        key = self._key(name, labels)
        with self._lock:
            self._histograms[key].append(value)
            # Keep last 1000 observations to bound memory
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-1000:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            uptime = time.time() - self._started_at
            return {
                "uptime_seconds": uptime,
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    k: {
                        "count": len(v),
                        "avg": sum(v) / len(v) if v else 0,
                        "min": min(v) if v else 0,
                        "max": max(v) if v else 0,
                        "p95": sorted(v)[int(len(v) * 0.95)] if v else 0,
                    }
                    for k, v in self._histograms.items()
                },
            }

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._started_at = time.time()


# Global registry
_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _registry


# Convenience helpers
def inc_counter(name: str, value: int = 1, **labels):
    _registry.inc(name, value, labels if labels else None)


def set_gauge(name: str, value: float, **labels):
    _registry.gauge(name, value, labels if labels else None)


def observe_histogram(name: str, value: float, **labels):
    _registry.observe(name, value, labels if labels else None)
