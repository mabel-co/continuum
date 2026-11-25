"""Continuum monitoring — structured logging for compliance operations.

Builds a logger that writes one JSON object per line (JSONL) so the compliance
operations — what was checked, what was found, any exceptions — can be grepped,
shipped to a log store, and parsed back by :func:`parse_compliance_log`.

Stdlib-only (``logging`` + ``json``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class _ComplianceFormatter(logging.Formatter):
    """Format each log record as one JSON line with ts, operation, and extras."""

    _STANDARD_ATTRS = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
            "traceback",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        operation = record.getMessage()
        payload: dict[str, object] = {
            "ts": ts,
            "operation": operation,
        }
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS and not key.startswith("_"):
                if key in ("exc_info", "exc_text", "traceback"):
                    continue
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def build_compliance_logger(name: str, log_path: str) -> logging.Logger:
    """Return a JSONL logger writing to ``log_path``.

    ``logger.info(operation, extra={...})`` writes one JSON line carrying the
    operation name plus every ``extra`` key (``tenant_id``, ``status``,
    ``error``, ``regulation_id``, ``source_connector``, ...) plus a ``ts``
    timestamp. Does not add a second handler if the logger already has one.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(_ComplianceFormatter())
    logger.addHandler(fh)
    return logger


def parse_compliance_log(lines: list[str]) -> list[dict]:
    """Parse JSONL log lines back into dicts, skipping blank lines."""
    return [json.loads(line) for line in lines if line.strip()]
