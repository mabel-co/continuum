"""Continuum integration — append-only JSONL notification bus.

File layout is one JSONL file per UTC calendar day, e.g.
``notifications/2026-09-05.jsonl``, stored under the base directory the
caller passes in.  Publishes are idempotent by ``event_id``: re-publishing
the same event is a no-op.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from .models import NotificationEvent


class JsonlNotificationBus:
    """Append-only JSONL bus with idempotent replay.

    Parameters
    ----------
    base_dir:
        Directory that contains the ``notifications/`` subdirectory.
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self._notif_dir = os.path.join(base_dir, "notifications")
        os.makedirs(self._notif_dir, exist_ok=True)

    # ── public API ──────────────────────────────────────────────────────

    def publish(self, event: NotificationEvent | dict) -> None:
        """Append *event* to the day file unless ``event_id`` already exists.

        If the event dict already has an ``event_id``, it is used directly.
        For :class:`NotificationEvent` instances ``to_dict()`` is called.
        """
        if isinstance(event, NotificationEvent):
            record: dict = event.to_dict()
        else:
            record = dict(event)

        event_id = record.get("event_id")
        if not event_id:
            raise ValueError("event must have an 'event_id' field")

        day = _pick_day(record)
        path = self._day_path(day)

        if self._event_id_exists(path, event_id):
            return  # idempotent no-op

        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load(self) -> list[dict]:
        """Return all notification events across all day files, in order."""
        results: list[dict] = []
        if not os.path.isdir(self._notif_dir):
            return results
        for name in sorted(os.listdir(self._notif_dir)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(self._notif_dir, name)
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped:
                        results.append(json.loads(stripped))
        return results

    def supersede(self, original_event_id: str, reason: str) -> None:
        """Write a supersede event referencing *original_event_id*.

        The new event carries a fresh ``event_id`` (``supersede-<original>``)
        and a ``supersedes`` field pointing back to the original.
        """
        supersede_event: dict = {
            "event_id": f"supersede-{original_event_id}",
            "notification_type": "supersede",
            "supersedes": original_event_id,
            "reason": reason,
            "activated_at": datetime.now(UTC).isoformat(),
        }
        self.publish(supersede_event)

    # ── internals ───────────────────────────────────────────────────────

    def _day_path(self, day: str) -> str:
        return os.path.join(self._notif_dir, f"{day}.jsonl")

    def _event_id_exists(self, path: str, event_id: str) -> bool:
        if not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    try:
                        rec = json.loads(stripped)
                        if rec.get("event_id") == event_id:
                            return True
                    except json.JSONDecodeError:
                        continue
        return False


def _pick_day(record: dict) -> str:
    """Extract the UTC date from *record* or default to today."""
    for key in ("activated_at", "ts"):
        val = record.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(str(val))
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass  # noqa: BLE001
    return datetime.now(UTC).strftime("%Y-%m-%d")
