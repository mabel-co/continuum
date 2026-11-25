"""Continuum monitoring — domain model.

Shared dataclass for one compliance operation event (a check, evidence
collection run, or report generation). Kept dependency-free so the metrics,
alerting, logging, and dashboard modules can all consume it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ComplianceOperationEvent:
    """One observability event for a compliance operation.

    Attributes:
        operation: Operation name, e.g. ``"compliance_check"``,
            ``"evidence_collection"``, ``"report_generation"``.
        status: ``"success"`` or ``"error"``.
        tenant_id: Tenant the operation ran for (RLS-scoped data).
        regulation_id: Regulation in scope, when applicable.
        error: Error message when ``status == "error"``.
        latency_ms: Wall-clock duration of the operation, if measured.
        at: Event timestamp (UTC).
    """

    operation: str
    status: str
    tenant_id: str
    regulation_id: str | None = None
    error: str | None = None
    latency_ms: float | None = None
    at: datetime | None = None
