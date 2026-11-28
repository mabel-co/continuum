"""Continuum integration — test suite.

Stdlib-only (unittest + tempfile).  These tests cover every acceptance
criterion in the integration spec (§9).

Run from the app root::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

# Ensure imports from the package root work when tests are discovered.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from continuum_integration.bus import JsonlNotificationBus
from continuum_integration.fanout import dispatch
from continuum_integration.models import (
    ACTIVATED,
    CONTENT_ACTIVATED,
    PENDING,
    NotificationEvent,
    RegulationChangeEvent,
)
from continuum_integration.publisher import build_payload, compute_sla_due_at

# ── helpers ─────────────────────────────────────────────────────────────────


def _make_event(
    event_id: str = "chng-20260828-001",
    regulation: str = "sr-26-2",
    status: str = ACTIVATED,
    old_hash: str = "sha256:abc123",
    new_hash: str = "sha256:def456",
    severity: str = "P0",
    activated_at: str | None = "2026-08-28T14:00:00Z",
    affected_records: list[str] | None = None,
    affected_controls: list[str] = ("ctrl-001",),
    notification_channels: list[str] = ("dashboard-update", "compliance-alert"),
) -> RegulationChangeEvent:
    return RegulationChangeEvent(
        event_id=event_id,
        regulation=regulation,
        old_hash=old_hash,
        new_hash=new_hash,
        triage_classification=severity,
        affected_records=affected_records or [f"{regulation}::section-vii::req-4"],
        affected_controls=list(affected_controls),
        activated_at=activated_at,
        notification_channels=list(notification_channels),
        status=status,
    )


SAMPLE_REGISTRY: dict[str, str] = {"sr-26-2": "v2.3", "eu-ai-act": "v1"}
# Fixture ids use SR 26-2 (live US model-risk guidance since 2026-04-17,
# superseding SR 11-7). The `section-vii::req-4` record
# paths are synthetic path-format samples, not citations of the letter's
# structure; the publisher derives source_paths from the regulation id alone.


# ── Payload building tests ──────────────────────────────────────────────────


class PayloadBuildTests(unittest.TestCase):
    """Acceptance criterion A + B: validation + happy path."""

    def test_happy_path(self) -> None:
        change = _make_event()
        payload = build_payload(change, SAMPLE_REGISTRY)

        self.assertIsInstance(payload, NotificationEvent)
        self.assertEqual(payload.event_id, "chng-20260828-001")
        self.assertEqual(payload.notification_type, CONTENT_ACTIVATED)
        self.assertEqual(payload.regulation, "sr-26-2")
        self.assertEqual(payload.version, "v2.3")
        self.assertEqual(payload.severity, "P0")
        self.assertEqual(payload.old_hash, "sha256:abc123")
        self.assertEqual(payload.new_hash, "sha256:def456")
        self.assertEqual(payload.affected_records, ["sr-26-2::section-vii::req-4"])
        self.assertEqual(payload.affected_controls, ["ctrl-001"])
        self.assertEqual(payload.changed_evidence_types, ["validation-report"])
        self.assertEqual(payload.activated_at, "2026-08-28T14:00:00Z")
        self.assertEqual(payload.sla_due_at, "2026-08-28T14:00:00Z")
        self.assertEqual(
            payload.source_paths,
            ["/knowledge/regulations/sr-26-2.json"],
        )
        self.assertEqual(
            payload.notification_channels,
            ["dashboard-update", "compliance-alert"],
        )
        # to_dict() round-trips
        d = payload.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["event_id"], "chng-20260828-001")

    def test_status_not_activated_raises(self) -> None:
        for bad_status in ("pending", "rejected", "quarantined", "in-progress"):
            change = _make_event(status=bad_status)
            with self.assertRaises(ValueError) as ctx:
                build_payload(change, SAMPLE_REGISTRY)
            self.assertIn(
                "status must be 'activated'",
                str(ctx.exception),
            )

    def test_empty_old_hash_raises(self) -> None:
        change = _make_event(old_hash="")
        with self.assertRaises(ValueError) as ctx:
            build_payload(change, SAMPLE_REGISTRY)
        self.assertIn("old_hash is required", str(ctx.exception))

    def test_empty_new_hash_raises(self) -> None:
        change = _make_event(new_hash="")
        with self.assertRaises(ValueError) as ctx:
            build_payload(change, SAMPLE_REGISTRY)
        self.assertIn("new_hash is required", str(ctx.exception))

    def test_malformed_event_id_raises(self) -> None:
        for bad_id in (
            "chng-20260828-1",  # 1-digit suffix
            "chng-20260828-0001",  # 4-digit suffix
            "CHNG-20260828-001",  # uppercase
            "chng-bad-date-001",  # non-date
            "not-an-event",  # completely wrong format
        ):
            change = _make_event(event_id=bad_id)
            with self.assertRaises(ValueError) as ctx:
                build_payload(change, SAMPLE_REGISTRY)
            self.assertIn("invalid event_id format", str(ctx.exception))

    def test_unknown_regulation_raises(self) -> None:
        change = _make_event(regulation="nonexistent-reg")
        with self.assertRaises(ValueError) as ctx:
            build_payload(change, SAMPLE_REGISTRY)
        self.assertIn("unknown regulation", str(ctx.exception))

    def test_no_registry_skip_check(self) -> None:
        """Without a registry, regulation validation is skipped."""
        change = _make_event(regulation="nonexistent-reg")
        payload = build_payload(change, None)
        self.assertEqual(payload.regulation, "nonexistent-reg")


# ── SLA computation tests ───────────────────────────────────────────────────


class SLATests(unittest.TestCase):
    """Acceptance criterion C: all five severity levels."""

    def test_p0_immediate(self) -> None:
        dt = datetime(2026, 8, 28, 14, 0, 0, tzinfo=UTC)
        result = compute_sla_due_at("P0", dt)
        self.assertEqual(result, dt)

    def test_p1_plus_4_hours(self) -> None:
        dt = datetime(2026, 8, 28, 14, 0, 0, tzinfo=UTC)
        result = compute_sla_due_at("P1", dt)
        expected = dt + timedelta(hours=4)
        self.assertEqual(result, expected)

    def test_p2_plus_24_hours(self) -> None:
        dt = datetime(2026, 8, 28, 14, 0, 0, tzinfo=UTC)
        result = compute_sla_due_at("P2", dt)
        expected = dt + timedelta(hours=24)
        self.assertEqual(result, expected)

    def test_p3_no_deadline(self) -> None:
        dt = datetime(2026, 8, 28, 14, 0, 0, tzinfo=UTC)
        self.assertIsNone(compute_sla_due_at("P3", dt))

    def test_na_no_deadline(self) -> None:
        dt = datetime(2026, 8, 28, 14, 0, 0, tzinfo=UTC)
        self.assertIsNone(compute_sla_due_at("N/A", dt))

    def test_p1_with_none_returns_none(self) -> None:
        # P1 with None activated_at cannot compute a future deadline
        self.assertIsNone(compute_sla_due_at("P1", None))


# ── Bus tests ────────────────────────────────────────────────────────────────


class BusTests(unittest.TestCase):
    """Acceptance criteria D (idempotency), E (ordering), F (supersede)."""

    def test_idempotency_duplicate_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = JsonlNotificationBus(tmp)
            event = _make_payload("chng-20260828-001")

            bus.publish(event)
            bus.publish(event)  # should be a no-op

            lines = bus.load()
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["event_id"], "chng-20260828-001")

    def test_append_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = JsonlNotificationBus(tmp)
            e1 = _make_payload("chng-20260828-001", day="2026-08-28")
            e2 = _make_payload("chng-20260829-001", day="2026-08-29")
            e3 = _make_payload("chng-20260828-002", day="2026-08-28")

            bus.publish(e1)
            bus.publish(e2)
            bus.publish(e3)

            loaded = bus.load()
            # e1 is on 2026-08-28, e2 on 2026-08-29, e3 on 2026-08-28
            # load() sorts by filename, so day 28 first, then 29
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[0]["event_id"], "chng-20260828-001")
            self.assertEqual(loaded[1]["event_id"], "chng-20260828-002")
            self.assertEqual(loaded[2]["event_id"], "chng-20260829-001")

    def test_supersede_links_to_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = JsonlNotificationBus(tmp)
            original = _make_payload("chng-20260828-001")
            bus.publish(original)
            bus.supersede("chng-20260828-001", "corrected activation")

            loaded = bus.load()
            self.assertEqual(len(loaded), 2)
            sup = loaded[1]
            self.assertEqual(sup["notification_type"], "supersede")
            self.assertEqual(sup["supersedes"], "chng-20260828-001")

    def test_different_days_different_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = JsonlNotificationBus(tmp)
            e1 = _make_payload("chng-20260828-001", day="2026-08-28")
            e2 = _make_payload("chng-20260829-001", day="2026-08-29")

            bus.publish(e1)
            bus.publish(e2)

            notif_dir = os.path.join(tmp, "notifications")
            files = sorted(os.listdir(notif_dir))
            self.assertEqual(files, ["2026-08-28.jsonl", "2026-08-29.jsonl"])


# ── Fan-out tests ───────────────────────────────────────────────────────────


class FanoutTests(unittest.TestCase):
    """Acceptance criteria G (isolation), H (report correctness),
    I (publisher rejection propagation)."""

    def test_happy_path_all_delivered(self) -> None:
        events: list[dict] = []
        consumers = [
            ("dashboard", lambda e: events.append(e)),
            ("compliance_alerting", lambda e: events.append(e)),
            ("report_generator", lambda e: events.append(e)),
            ("evidence_collection", lambda e: events.append(e)),
        ]
        report = dispatch(_make_payload("chng-20260828-001"), consumers)

        self.assertEqual(
            report.delivered,
            ["dashboard", "compliance_alerting", "report_generator", "evidence_collection"],
        )
        self.assertEqual(report.failed, [])
        self.assertEqual(len(events), 4)

    def test_one_failing_consumer_isolation(self) -> None:
        def boom(_: dict) -> None:
            raise RuntimeError("dashboard unreachable")

        consumers = [
            ("dashboard", boom),
            ("compliance_alerting", lambda e: None),
            ("report_generator", lambda e: None),
            ("evidence_collection", lambda e: None),
        ]
        report = dispatch(_make_payload("chng-20260828-001"), consumers)

        self.assertEqual(
            report.delivered,
            ["compliance_alerting", "report_generator", "evidence_collection"],
        )
        self.assertEqual(len(report.failed), 1)
        self.assertEqual(report.failed[0][0], "dashboard")
        self.assertIn("dashboard unreachable", report.failed[0][1])

    def test_publisher_rejection_propagates(self) -> None:
        """A bad activation must not be published — ValueError is raised."""
        change = _make_event(status=PENDING)
        with self.assertRaises(ValueError) as ctx:
            build_payload(change, SAMPLE_REGISTRY)
        self.assertIn("status must be 'activated'", str(ctx.exception))


# ── CLI tests ────────────────────────────────────────────────────────────────


class CLITests(unittest.TestCase):
    """Acceptance criterion I: end-to-end CLI behaviour.

    Test scenarios:
    - 2 activated + 1 rejected → exactly 2 published, exit 0
    - malformed record → exit non-zero
    """

    def _write_change_log(self, records: list[dict]) -> str:
        """Write a temporary change-log file."""
        path = tempfile.mktemp(suffix=".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"change_log": records}, fh)
        return path

    def test_two_activated_one_rejected(self) -> None:
        records = [
            {
                "event_id": "chng-20260828-001",
                "regulation": "sr-26-2",
                "old_hash": "sha256:abc",
                "new_hash": "sha256:def",
                "triage_classification": "P0",
                "affected_records": ["sr-26-2::section-vii::req-4"],
                "affected_controls": ["ctrl-001"],
                "activated_at": "2026-08-28T14:00:00Z",
                "notification_channels": ["dashboard-update", "compliance-alert"],
                "status": "activated",
            },
            {
                "event_id": "chng-20260828-002",
                "regulation": "eu-ai-act",
                "old_hash": "sha256:aaa",
                "new_hash": "sha256:bbb",
                "triage_classification": "P1",
                "affected_records": ["eu-ai-act::title-3::article-9"],
                "affected_controls": ["ctrl-002"],
                "activated_at": "2026-08-28T15:00:00Z",
                "notification_channels": ["dashboard-update"],
                "status": "activated",
            },
            {
                "event_id": "chng-20260828-003",
                "regulation": "fca-ml",
                "old_hash": "sha256:ccc",
                "new_hash": "sha256:ddd",
                "triage_classification": "P2",
                "affected_records": [],
                "affected_controls": [],
                "status": "rejected",
            },
        ]
        cl_path = self._write_change_log(records)

        with tempfile.TemporaryDirectory() as tmp:
            from continuum_integration.__main__ import main

            rc = main(["publish", "--change-log", cl_path, "--base-dir", tmp])

            self.assertEqual(rc, 0)
            # Verify two events were in the bus
            bus = JsonlNotificationBus(tmp)
            loaded = bus.load()
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["event_id"], "chng-20260828-001")
            self.assertEqual(loaded[1]["event_id"], "chng-20260828-002")

    def test_malformed_record_exits_nonzero(self) -> None:
        records = [
            {
                "event_id": "chng-bad-date-001",  # malformed
                "regulation": "sr-26-2",
                "old_hash": "sha256:abc",
                "new_hash": "sha256:def",
                "status": "activated",
                "affected_records": [],
                "affected_controls": [],
            },
        ]
        cl_path = self._write_change_log(records)

        with tempfile.TemporaryDirectory() as tmp:
            from continuum_integration.__main__ import main

            rc = main(["publish", "--change-log", cl_path, "--base-dir", tmp])

            self.assertNotEqual(rc, 0)

        os.unlink(cl_path)


# ── helpers for test construction ──────────────────────────────────────────


def _make_payload(
    event_id: str = "chng-20260828-001",
    day: str = "2026-08-28",
) -> dict:
    """Create a minimal notification event dict for bus/fanout tests."""
    return {
        "event_id": event_id,
        "notification_type": CONTENT_ACTIVATED,
        "regulation": "sr-26-2",
        "version": "v2.3",
        "severity": "P0",
        "old_hash": "sha256:abc123",
        "new_hash": "sha256:def456",
        "affected_records": ["sr-26-2::section-vii::req-4"],
        "affected_controls": ["ctrl-001"],
        "changed_evidence_types": ["validation-report"],
        "activated_at": f"{day}T14:00:00+00:00",
        "sla_due_at": f"{day}T14:00:00+00:00",
        "source_paths": ["/knowledge/regulations/sr-26-2.json"],
        "notification_channels": ["dashboard-update", "compliance-alert"],
    }


if __name__ == "__main__":
    unittest.main()
