"""Continuum monitoring — alert evaluation and notification.

Rules are plain callables over :class:`Metrics`; evaluation walks the rule
list, honours a per-rule cooldown so a broken state does not re-notify every
tick, and delivers each fired alert through one or more notifiers
(console, JSONL file, webhook). A rule that raises is isolated: it is counted
in :attr:`AlertState.rule_errors` and evaluation continues.

Event-driven failures (e.g. an evidence collection run erroring) are not
polled — the integration layer calls :func:`fire_immediate_alert` when the
failure is observed, which notifies with no cooldown.

Stdlib-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.request import Request, urlopen

from continuum_monitoring.metrics import Metrics

HISTORY_LIMIT = 50


@dataclass
class AlertRule:
    """One alert rule.

    Attributes:
        rule_id: Stable identifier (used for cooldowns and reports).
        predicate: Returns True when the alert fires.
        severity: ``"warning"`` or ``"critical"`` (default ``"warning"``).
        description: Human-readable statement of the condition.
    """

    rule_id: str
    predicate: Callable[[Metrics], bool]
    severity: str = "warning"
    description: str = ""


@dataclass
class AlertState:
    """Mutable evaluation state shared across ticks.

    Attributes:
        fired: Mapping of rule_id to how many times it has fired.
        suppressed: Total alerts suppressed by cooldown.
        rule_errors: Mapping of rule_id to how many times it raised.
        history: Most recent fired-alert payloads (newest first).
        last_fires: Mapping of rule_id to the timestamp of its last fire
            (used for cooldowns).
    """

    fired: dict[str, int] = field(default_factory=dict)
    suppressed: int = 0
    rule_errors: dict[str, int] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    last_fires: dict[str, datetime] = field(default_factory=dict)


class ConsoleNotifier:
    """Prints fired alerts to stdout (the MVP default)."""

    def notify(self, alert: dict) -> None:
        severity = str(alert.get("severity", "warning")).upper()
        print(f"[{severity}] {alert.get('rule_id', '?')}: {alert.get('description', '')}")


class FileNotifier:
    """Appends one JSON line per fired alert to a file (JSONL)."""

    def __init__(self, path: str) -> None:
        self._path = path

    def notify(self, alert: dict) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert) + "\n")


class WebhookNotifier:
    """POSTs the alert payload as JSON to a URL (urllib, with timeout).

    Raises on connection failure — alerting must not silently drop a
    notification the team was supposed to receive; the caller decides how to
    surface delivery failures.
    """

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout

    def notify(self, alert: dict) -> None:
        request = Request(self._url, data=json.dumps(alert).encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        with urlopen(request, timeout=self._timeout):
            pass


def _metrics_snapshot(metrics: Metrics) -> dict:
    """A compact, JSON-serialisable view of current metrics for alert payloads."""
    return {
        "counters": metrics.counters,
        "error_rate": metrics.error_rate,
        "latency_p50_ms": metrics.latency_ms.p50,
        "latency_p95_ms": metrics.latency_ms.p95,
        "uptime_minutes": metrics.uptime_minutes,
    }


DEFAULT_RULES: list[AlertRule] = [
    AlertRule(
        "high-error-rate",
        lambda m: m.error_rate > 0.25 and m.counters.get("events_total", 0) >= 5,
        severity="critical",
        description="Overall error rate above 25% with at least 5 events",
    ),
    AlertRule(
        "latency-p95",
        lambda m: m.latency_ms.p95 is not None and m.latency_ms.p95 > 2000,
        severity="warning",
        description="p95 latency above 2000 ms",
    ),
]


def evaluate_alerts(
    metrics: Metrics,
    rules: list[AlertRule],
    state: AlertState,
    notifiers: list,
    cooldown_s: float,
    now: datetime | None = None,
) -> list[dict]:
    """Evaluate all rules against current metrics.

    For each rule whose predicate holds, fire unless the rule is inside its
    cooldown window; fired alerts are delivered to every notifier and returned
    in rule order. Rules that raise are counted and skipped. ``now`` defaults
    to the current UTC time and is injectable for tests.
    """
    now = now or datetime.now(UTC)
    fired_alerts: list[dict] = []
    for rule in rules:
        try:
            triggered = bool(rule.predicate(metrics))
        except Exception:
            state.rule_errors[rule.rule_id] = state.rule_errors.get(rule.rule_id, 0) + 1
            continue
        if not triggered:
            continue
        last = state.last_fires.get(rule.rule_id)
        if last is not None and (now - last).total_seconds() < cooldown_s:
            state.suppressed += 1
            continue
        state.last_fires[rule.rule_id] = now
        state.fired[rule.rule_id] = state.fired.get(rule.rule_id, 0) + 1
        payload = {
            "rule_id": rule.rule_id,
            "severity": rule.severity,
            "description": rule.description,
            "ts": now.isoformat(),
            "metrics_snapshot": _metrics_snapshot(metrics),
        }
        state.history.insert(0, payload)
        del state.history[HISTORY_LIMIT:]
        for notifier in notifiers:
            notifier.notify(payload)
        fired_alerts.append(payload)
    return fired_alerts


def fire_immediate_alert(
    state: AlertState,
    notifiers: list,
    rule_id: str,
    severity: str,
    description: str,
    detail: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Fire an event-driven alert with no cooldown.

    Used for failures that must be reported the moment they are observed —
    e.g. an ``evidence_collection`` run ending in error — rather than waiting
    for the next :func:`evaluate_alerts` tick.
    """
    now = now or datetime.now(UTC)
    state.fired[rule_id] = state.fired.get(rule_id, 0) + 1
    payload = {
        "rule_id": rule_id,
        "severity": severity,
        "description": description,
        "ts": now.isoformat(),
        "detail": detail or {},
    }
    state.history.insert(0, payload)
    del state.history[HISTORY_LIMIT:]
    for notifier in notifiers:
        notifier.notify(payload)
    return payload
