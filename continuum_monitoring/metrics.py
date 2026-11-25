"""Continuum monitoring — in-memory metrics.

Aggregates compliance operation events into the counters and percentiles the
dashboard and alert rules read: total/success/error counts, error rate,
per-operation breakdown, latency percentiles (p50/p95), and uptime.

Stdlib-only. The MVP runs one process (Docker Compose), so a single in-memory
aggregator is the right-sized primitive; a later version can swap this for
Prometheus client instrumentation without changing the alert/dashboard API.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from continuum_monitoring.models import ComplianceOperationEvent


class _LatencyPercentiles:
    """Thin object exposing ``.p50`` / ``.p95`` for latency values."""

    def __init__(self, p50: float | None, p95: float | None) -> None:
        self.p50 = p50
        self.p95 = p95


def _percentile(sorted_values: list[float], q: float) -> float:
    """Compute quantile ``q`` over *sorted_values* via linear interpolation."""
    n = len(sorted_values)
    pos = (n - 1) * q / 100
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    frac = pos - lo
    if lo == hi:
        return sorted_values[lo]
    x = sorted_values[lo]
    y = sorted_values[hi]
    return x + frac * (y - x)


def _as_utc(ts: datetime) -> datetime:
    """Return *ts* with a UTC timezone (naive timestamps are assumed UTC)."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


class Metrics:
    """In-memory aggregator of compliance operation events.

    Call :meth:`record` for each event; read the attributes for the current
    view. All latency values are in milliseconds.

    Attributes (read-only, computed at access time):
        counters: Dict of aggregate counters (``events_total``,
            ``events_success``, ``errors_total``, ``latency_samples``,
            ``tenants_seen``).
        error_rate: ``errors_total / events_total`` (0.0 when empty).
        latency_ms: Object with ``.p50`` / ``.p95`` (``None`` when there are
            no samples), computed by linear interpolation between the two
            closest ranks: for a quantile ``q`` on ``n`` sorted samples,
            ``pos = (n - 1) * q / 100`` and the value is
            ``x + frac * (y - x)`` where ``x`` / ``y`` are the samples at
            ``floor(pos)`` / ``ceil(pos)`` (single sample returns itself).
        per_operation: Mapping of operation name to its success/error counts.
        uptime_minutes: Minutes between the first and last recorded event
            (``None`` when fewer than two events).
    """

    def __init__(self) -> None:
        self._events_total: int = 0
        self._events_success: int = 0
        self._errors_total: int = 0
        self._latency_samples: list[float] = []
        self._tenants: set[str] = set()
        self._per_operation: dict[str, dict[str, int]] = {}
        self._timestamps: list[datetime] = []

    @property
    def counters(self) -> dict[str, int]:
        """Aggregate counters."""
        if self._events_total == 0:
            return {}
        return {
            "events_total": self._events_total,
            "events_success": self._events_success,
            "errors_total": self._errors_total,
            "latency_samples": len(self._latency_samples),
            "tenants_seen": len(self._tenants),
        }

    @property
    def error_rate(self) -> float:
        """``errors_total / events_total`` or 0.0 when empty."""
        if self._events_total == 0:
            return 0.0
        return self._errors_total / self._events_total

    @property
    def latency_ms(self) -> _LatencyPercentiles:
        """Object with ``.p50`` / ``.p95`` (``None`` when no samples)."""
        if not self._latency_samples:
            return _LatencyPercentiles(None, None)
        sorted_lat = sorted(self._latency_samples)
        n = len(sorted_lat)
        if n == 1:
            return _LatencyPercentiles(sorted_lat[0], sorted_lat[0])
        return _LatencyPercentiles(
            _percentile(sorted_lat, 50),
            _percentile(sorted_lat, 95),
        )

    @property
    def per_operation(self) -> dict[str, dict[str, int]]:
        """Mapping of operation name to its success/error counts."""
        return {op: dict(counts) for op, counts in self._per_operation.items()}

    @property
    def uptime_minutes(self) -> float | None:
        """Minutes between first and last recorded event (``None`` when fewer than 2)."""
        if len(self._timestamps) < 2:
            return None
        clean = [_as_utc(ts) for ts in self._timestamps]
        return (max(clean) - min(clean)).total_seconds() / 60

    def is_healthy(self) -> bool:
        """True when at least one event has been recorded."""
        return self._events_total >= 1

    def record(self, ev: ComplianceOperationEvent) -> None:
        """Add one event to the aggregate."""
        self._events_total += 1
        self._tenants.add(ev.tenant_id)
        counts = self._per_operation.setdefault(ev.operation, {"success": 0, "error": 0})
        if ev.status == "success":
            self._events_success += 1
            counts["success"] += 1
        else:
            self._errors_total += 1
            counts["error"] += 1
        if ev.latency_ms is not None:
            self._latency_samples.append(ev.latency_ms)
        if ev.at is not None:
            self._timestamps.append(ev.at)


def new_event(
    operation: str,
    status: str,
    tenant_id: str,
    regulation_id: str | None = None,
    error: str | None = None,
    latency_ms: float | None = None,
    at: datetime | None = None,
) -> ComplianceOperationEvent:
    """Build a :class:`ComplianceOperationEvent` (default: now, UTC)."""
    return ComplianceOperationEvent(
        operation=operation,
        status=status,
        tenant_id=tenant_id,
        regulation_id=regulation_id,
        error=error,
        latency_ms=latency_ms,
        at=at if at is not None else datetime.now(UTC),
    )
