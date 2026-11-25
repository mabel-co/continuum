"""Continuum integration — domain models.

Frozen dataclasses representing change-log records (the 4d input contract) and
notification events (the canonical JSON payload).  Also defines the trigger-type
and status constants used throughout the package.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Notification trigger types (six from 4d §3.5) ──────────────────────────

CONTENT_ACTIVATED = "content_activated"
CONTROL_STATUS_NEEDS_REVIEW = "control_status_needs_review"
EVIDENCE_TYPE_CHANGE = "evidence_type_change"
REPORT_TEMPLATE_UPDATE = "report_template_update"
REGULATION_ADDED = "regulation_added"
REGULATION_REMOVED_SUPERSEDED = "regulation_removed_superseded"

TRIGGER_TYPES: tuple[str, ...] = (
    CONTENT_ACTIVATED,
    CONTROL_STATUS_NEEDS_REVIEW,
    EVIDENCE_TYPE_CHANGE,
    REPORT_TEMPLATE_UPDATE,
    REGULATION_ADDED,
    REGULATION_REMOVED_SUPERSEDED,
)

# ── Change-log status constants ─────────────────────────────────────────────

PENDING = "pending"
IN_PROGRESS = "in-progress"
ACTIVATED = "activated"
REJECTED = "rejected"
QUARANTINED = "quarantined"

CHANGE_LOG_STATUSES: tuple[str, ...] = (
    PENDING,
    IN_PROGRESS,
    ACTIVATED,
    REJECTED,
    QUARANTINED,
)


@dataclass(frozen=True)
class RegulationChangeEvent:
    """A single change-log record (the 4d input contract, §2).

    Only the fields 4e consumes are stored; the rest of the change-log
    schema is ignored by this package.
    """

    event_id: str
    regulation: str
    old_hash: str
    new_hash: str
    triage_classification: str
    affected_records: list[str]
    affected_controls: list[str]
    activated_at: str | None = None
    notification_channels: list[str] = ()
    status: str = ACTIVATED


@dataclass(frozen=True)
class NotificationEvent:
    """Canonical notification payload (3.2).

    Build via :func:`publisher.build_payload`; serialize with ``to_dict()``.
    """

    event_id: str
    notification_type: str
    regulation: str
    version: str
    severity: str
    old_hash: str
    new_hash: str
    affected_records: list[str]
    affected_controls: list[str]
    changed_evidence_types: list[str]
    activated_at: str
    sla_due_at: str
    source_paths: list[str]
    notification_channels: list[str]

    def to_dict(self) -> dict:
        """Return the notification as a plain dict suitable for JSON serialisation."""
        return {
            "event_id": self.event_id,
            "notification_type": self.notification_type,
            "regulation": self.regulation,
            "version": self.version,
            "severity": self.severity,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "affected_records": self.affected_records,
            "affected_controls": self.affected_controls,
            "changed_evidence_types": self.changed_evidence_types,
            "activated_at": self.activated_at,
            "sla_due_at": self.sla_due_at,
            "source_paths": self.source_paths,
            "notification_channels": self.notification_channels,
        }
