"""Continuum MVP monitoring & observability — test suite.

Stdlib-only (unittest). These tests define the contracts that
`apps/continuum-monitoring/continuum_monitoring/` must satisfy.

Run from the app root:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from continuum_monitoring.alerting import (
    AlertRule,
    AlertState,
    ConsoleNotifier,
    FileNotifier,
    WebhookNotifier,
    evaluate_alerts,
)
from continuum_monitoring.dashboard import render_dashboard_html
from continuum_monitoring.logging_setup import build_compliance_logger, parse_compliance_log
from continuum_monitoring.metrics import Metrics, new_event
from continuum_monitoring.models import ComplianceOperationEvent


def event(
    op: str = "compliance_check",
    status: str = "success",
    latency_ms: float | None = None,
    tenant: str = "t1",
    when: datetime | None = None,
) -> ComplianceOperationEvent:
    return new_event(
        operation=op,
        status=status,
        tenant_id=tenant,
        latency_ms=latency_ms,
        at=when or datetime.now(UTC),
    )


class MetricsTests(unittest.TestCase):
    def test_empty_metrics(self) -> None:
        m = Metrics()
        self.assertEqual(m.counters, {})
        self.assertAlmostEqual(m.error_rate, 0.0)
        self.assertIsNone(m.latency_ms.p50)
        self.assertIsNone(m.latency_ms.p95)
        self.assertFalse(m.is_healthy())

    def test_record_success_updates_counters_and_latency(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "success", 120.0))
        m.record(event("compliance_check", "success", 240.0))
        m.record(event("evidence_collection", "success", 60.0))
        self.assertEqual(m.counters["events_total"], 3)
        self.assertEqual(m.counters["events_success"], 3)
        self.assertEqual(m.counters["errors_total"], 0)
        self.assertAlmostEqual(m.error_rate, 0.0)
        self.assertAlmostEqual(m.latency_ms.p50, 120.0)
        self.assertAlmostEqual(m.latency_ms.p95, 228.0)
        self.assertIn("evidence_collection", m.per_operation)
        self.assertEqual(m.per_operation["evidence_collection"]["success"], 1)
        self.assertTrue(m.is_healthy())

    def test_error_rate_and_per_operation_errors(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "success", 10.0))
        m.record(event("compliance_check", "error", 20.0))
        m.record(event("evidence_collection", "error", 30.0, tenant="t2"))
        self.assertEqual(m.counters["errors_total"], 2)
        self.assertAlmostEqual(m.error_rate, 2 / 3)
        self.assertEqual(m.per_operation["compliance_check"]["error"], 1)
        self.assertEqual(m.per_operation["evidence_collection"]["error"], 1)
        self.assertEqual(m.counters["tenants_seen"], 2)
        # p95 over [10, 20, 30]
        self.assertAlmostEqual(m.latency_ms.p95, 29.0)

    def test_missing_latency_is_ignored_in_percentiles(self) -> None:
        m = Metrics()
        m.record(event(status="success", latency_ms=None))
        self.assertIsNone(m.latency_ms.p50)
        self.assertEqual(m.counters["latency_samples"], 0)

    def test_uptime(self) -> None:
        m = Metrics()
        now = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
        m.record(event(when=now))
        m.record(event(when=now + timedelta(minutes=30)))
        self.assertAlmostEqual(m.uptime_minutes, 30.0)


def _p95_rule(threshold_ms: float = 5000) -> AlertRule:
    return AlertRule(
        "latency-p95",
        lambda x: x.latency_ms.p95 is not None and x.latency_ms.p95 > threshold_ms,
    )


class AlertingTests(unittest.TestCase):
    def _healthy_metrics(self) -> Metrics:
        m = Metrics()
        for _ in range(10):
            m.record(event("compliance_check", "success", 100.0))
        return m

    def test_healthy_metrics_fire_nothing(self) -> None:
        rules = [
            AlertRule("high-error-rate", lambda x: x.error_rate > 0.5),
            _p95_rule(),
        ]
        state = AlertState()
        fired = evaluate_alerts(
            self._healthy_metrics(), rules, state, [ConsoleNotifier()], cooldown_s=60
        )
        self.assertEqual(fired, [])
        self.assertEqual(state.fired, {})

    def test_broken_app_fires_error_rate_alert(self) -> None:
        m = Metrics()
        for _ in range(5):
            m.record(event("compliance_check", "error", 10.0))
        rules = [AlertRule("high-error-rate", lambda x: x.error_rate > 0.5, severity="critical")]
        state = AlertState()
        fired = evaluate_alerts(m, rules, state, [ConsoleNotifier()], cooldown_s=60)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["rule_id"], "high-error-rate")
        self.assertEqual(fired[0]["severity"], "critical")
        self.assertEqual(state.fired["high-error-rate"], 1)

    def test_latency_breach_fires(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "success", 9000.0))
        rules = [_p95_rule()]
        fired = evaluate_alerts(m, rules, AlertState(), [ConsoleNotifier()], cooldown_s=60)
        self.assertEqual([a["rule_id"] for a in fired], ["latency-p95"])

    def test_cooldown_suppresses_repeat_within_window(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "error", 10.0))
        rules = [AlertRule("high-error-rate", lambda x: x.error_rate > 0.5)]
        state = AlertState()
        fired = evaluate_alerts(m, rules, state, [ConsoleNotifier()], cooldown_s=300)
        self.assertEqual(len(fired), 1)
        # Same state, still inside the cooldown window: suppressed.
        fired = evaluate_alerts(m, rules, state, [ConsoleNotifier()], cooldown_s=300)
        self.assertEqual(fired, [])
        self.assertEqual(state.suppressed, 1)

    def test_file_notifier_writes_jsonl(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "error", 10.0))
        rules = [AlertRule("high-error-rate", lambda x: x.error_rate > 0.5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/alerts.jsonl"
            notifier = FileNotifier(path)
            fired = evaluate_alerts(m, rules, AlertState(), [notifier], cooldown_s=60)
            self.assertEqual(len(fired), 1)
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["rule_id"], "high-error-rate")
            self.assertIn("ts", lines[0])

    def test_webhook_notifier_posts_json(self) -> None:
        captured: list[dict] = []

        class FakeResponse:
            def __init__(self, status: int) -> None:
                self.status = status
                self.body = io.BytesIO(b"ok")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                captured.append({"status": self.status})
                return False

        from urllib.request import Request

        calls: list[Request] = []

        def fake_urlopen(req: Request, timeout: float):
            calls.append(req)
            return FakeResponse(200)

        import continuum_monitoring.alerting as alerting

        original = alerting.urlopen
        alerting.urlopen = fake_urlopen
        try:
            m = Metrics()
            m.record(event("compliance_check", "error", 10.0))
            rules = [AlertRule("high-error-rate", lambda x: x.error_rate > 0.5)]
            notifier = WebhookNotifier("http://example.invalid/hook", timeout=5)
            fired = evaluate_alerts(m, rules, AlertState(), [notifier], cooldown_s=60)
            self.assertEqual(len(fired), 1)
            self.assertEqual(len(calls), 1)
            body = json.loads(calls[0].data.decode("utf-8"))
            self.assertEqual(body["rule_id"], "high-error-rate")
        finally:
            alerting.urlopen = original

    def test_rule_failure_is_isolated(self) -> None:
        def boom(x: Metrics) -> bool:
            raise RuntimeError("rule crashed")

        m = self._healthy_metrics()
        state = AlertState()
        fired = evaluate_alerts(
            m,
            [AlertRule("broken", boom), AlertRule("high-error-rate", lambda x: x.error_rate > 0.5)],
            state,
            [ConsoleNotifier()],
            cooldown_s=60,
        )
        # The broken rule must not take down evaluation; the healthy rule still
        # evaluates (no alert expected here) and the failure is recorded.
        self.assertEqual(fired, [])
        self.assertEqual(state.rule_errors, {"broken": 1})


class DashboardTests(unittest.TestCase):
    def test_render_dashboard_html(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "success", 100.0))
        m.record(event("compliance_check", "error", 900.0))
        state = AlertState()
        html = render_dashboard_html(m, state, title="Continuum operational view")
        self.assertIn("Continuum operational view", html)
        self.assertIn("<html", html)
        self.assertIn("high-error-rate", html)  # alert rules table
        self.assertIn("compliance_check", html)  # per-operation table
        self.assertIn("events_total", html)  # counters table

    def test_render_includes_alert_history(self) -> None:
        m = Metrics()
        m.record(event("compliance_check", "error", 10.0))
        rules = [AlertRule("high-error-rate", lambda x: x.error_rate > 0.5)]
        state = AlertState()
        fired = evaluate_alerts(m, rules, state, [ConsoleNotifier()], cooldown_s=60)
        html = render_dashboard_html(m, state)
        self.assertEqual(len(fired), 1)
        self.assertIn("high-error-rate", html)


class LoggingTests(unittest.TestCase):
    def test_build_compliance_logger_emits_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/compliance.log"
            logger = build_compliance_logger("test", path)
            logger.info(
                "evidence_collection",
                extra={
                    "tenant_id": "t1",
                    "status": "error",
                    "error": "connector timeout",
                    "regulation_id": "sr-26-2",
                    "source_connector": "servicenow",
                },
            )
            with open(path, encoding="utf-8") as fh:
                line = json.loads(fh.readline())
            self.assertEqual(line["operation"], "evidence_collection")
            self.assertEqual(line["tenant_id"], "t1")
            self.assertEqual(line["status"], "error")
            self.assertIn("ts", line)

    def test_parse_compliance_log_roundtrip(self) -> None:
        records = parse_compliance_log(_sample_log_lines())
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["operation"], "evidence_collection")
        self.assertEqual(records[1]["status"], "error")
        self.assertEqual(records[2]["tenant_id"], "t2")


def _sample_log_lines() -> list[str]:
    base = {
        "ts": "2026-08-26T12:00:00+00:00",
        "tenant_id": "t1",
        "operation": "evidence_collection",
        "status": "success",
    }
    rows = [
        dict(base),
        dict(base, operation="compliance_check", status="error", error="boom"),
        dict(base, tenant_id="t2", operation="report_generation", status="success"),
    ]
    return [json.dumps(r) for r in rows]


if __name__ == "__main__":
    unittest.main()
