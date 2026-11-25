"""Continuum integration — CLI entry point.

Usage::

    python -m continuum_integration publish \\
        --change-log change-log.json \\
        --base-dir ./meta
"""

from __future__ import annotations

import argparse
import json
import sys

from .bus import JsonlNotificationBus
from .models import RegulationChangeEvent
from .publisher import build_payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="continuum_integration",
        description="Publish activated change-log records to the notification bus.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pub = sub.add_parser("publish", help="Publish activated records.")
    pub.add_argument(
        "--change-log",
        required=True,
        help='Path to the 4d change-log JSON file (schema: {"change_log": [...]})',
    )
    pub.add_argument(
        "--base-dir",
        required=True,
        help="Base directory for the notification bus (e.g. ./meta).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point — returns 0 on success, 1 on any validation failure."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "publish":
        return _cmd_publish(args)

    return 1


def _cmd_publish(args: argparse.Namespace) -> int:
    change_log_path = args.change_log
    base_dir = args.base_dir

    with open(change_log_path, encoding="utf-8") as fh:
        data = json.load(fh)

    records = data.get("change_log", [])
    bus = JsonlNotificationBus(base_dir)
    exit_code = 0
    published_count = 0
    skipped_count = 0

    for rec in records:
        try:
            if rec.get("status") != "activated":
                reason = f"status is {rec.get('status', 'unknown')}, not activated"
                print(
                    json.dumps(
                        {
                            "event_id": rec.get("event_id"),
                            "reason": reason,
                        }
                    ),
                    file=sys.stdout,
                )
                skipped_count += 1
                continue

            change = _to_change_event(rec)
            payload = build_payload(change)
            bus.publish(payload)
            print(
                json.dumps(payload.to_dict()),
                file=sys.stdout,
            )
            published_count += 1
        except ValueError as exc:
            print(
                json.dumps(
                    {
                        "event_id": rec.get("event_id"),
                        "error": str(exc),
                    }
                ),
                file=sys.stdout,
            )
            exit_code = 1

    return exit_code


def _to_change_event(rec: dict) -> RegulationChangeEvent:
    """Map a raw change-log dict to a :class:`RegulationChangeEvent`."""
    return RegulationChangeEvent(
        event_id=rec["event_id"],
        regulation=rec.get("regulation", ""),
        old_hash=rec.get("old_hash", ""),
        new_hash=rec.get("new_hash", ""),
        triage_classification=rec.get("triage_classification", "N/A"),
        affected_records=rec.get("affected_records", []),
        affected_controls=rec.get("affected_controls", []),
        activated_at=rec.get("activated_at"),
        notification_channels=rec.get("notification_channels", []),
        status=rec.get("status", "activated"),
    )


if __name__ == "__main__":
    sys.exit(main())
