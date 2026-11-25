"""Continuum integration — event publisher.

Takes a validated, ``activated`` change-log record and produces a
:class:`NotificationEvent` on the canonical JSONL bus.  Validates inputs and
raises :class:`ValueError` on every contract violation.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import (
    ACTIVATED,
    CONTENT_ACTIVATED,
    NotificationEvent,
    RegulationChangeEvent,
)

EVENT_ID_RE = re.compile(r"^chng-\d{8}-\d{3}$")


def build_payload(
    change_event: RegulationChangeEvent,
    registry: dict[str, str] | None = None,
) -> NotificationEvent:
    """Build a :class:`NotificationEvent` from a change-log record.

    Parameters
    ----------
    change_event:
        A :class:`RegulationChangeEvent` whose ``status`` is ``activated``.
    registry:
        Optional dict mapping regulation IDs to their current version string.
        When provided, the regulation ID is validated against it.

    Returns
    -------
    A :class:`NotificationEvent` ready for the JSONL bus.

    Raises
    ------
    ValueError
        On any contract violation (see §3.3 of the integration spec).
    """
    _validate(change_event, registry)

    severity = change_event.triage_classification

    if change_event.activated_at:
        activated_dt: datetime | None = _parse_iso(change_event.activated_at)
    else:
        activated_dt = None

    sla_due = compute_sla_due_at(severity, activated_dt)
    sla_due_str = _to_iso(sla_due) if sla_due else ""

    version = ""
    if registry and change_event.regulation in registry:
        version = registry[change_event.regulation]

    source_paths = [f"/knowledge/regulations/{change_event.regulation}.json"]

    return NotificationEvent(
        event_id=change_event.event_id,
        notification_type=CONTENT_ACTIVATED,
        regulation=change_event.regulation,
        version=version,
        severity=severity,
        old_hash=change_event.old_hash,
        new_hash=change_event.new_hash,
        affected_records=list(change_event.affected_records),
        affected_controls=list(change_event.affected_controls),
        changed_evidence_types=["validation-report"],
        activated_at=change_event.activated_at or "",
        sla_due_at=sla_due_str,
        source_paths=source_paths,
        notification_channels=list(change_event.notification_channels),
    )


def compute_sla_due_at(severity: str, activated_at: datetime | None) -> datetime | None:
    """Return the SLA deadline for *severity* at *activated_at*.

    | Severity | SLA |
    |----------|-----|
    | P0       | Immediate (``activated_at`` itself) |
    | P1       | +4 hours |
    | P2       | +24 hours |
    | P3       | No deadline (``None``) |
    | N/A      | No deadline (``None``) |
    """
    if severity == "P0":
        return activated_at
    if severity == "P1" and activated_at is not None:
        return activated_at + timedelta(hours=4)
    if severity == "P2" and activated_at is not None:
        return activated_at + timedelta(hours=24)
    return None


def _validate(
    change_event: RegulationChangeEvent,
    registry: dict[str, str] | None,
) -> None:
    """Raise ``ValueError`` on every contract violation."""
    if change_event.status != ACTIVATED:
        raise ValueError("status must be 'activated' to publish a notification")

    if not change_event.old_hash:
        raise ValueError("old_hash is required")

    if not change_event.new_hash:
        raise ValueError("new_hash is required")

    if not EVENT_ID_RE.match(change_event.event_id):
        raise ValueError("invalid event_id format: expected chng-YYYYMMDD-NNN")

    if registry is not None and change_event.regulation not in registry:
        raise ValueError(f"unknown regulation: {change_event.regulation}")


def _parse_iso(s: str) -> datetime | None:
    """Parse an ISO-8601 datetime string (UTC).  Returns None on failure."""
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None  # noqa: BLE001


def _to_iso(dt: datetime) -> str:
    """Return an ISO-8601 string with ``Z`` suffix for UTC times."""
    s = dt.isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s
