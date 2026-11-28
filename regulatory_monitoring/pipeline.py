"""Detection pipeline: state file, change log, and run orchestration.

The state file (JSON) maps ``source_id -> sha256 hex of last-seen normalised
content``. The change log (JSONL) carries one record per detection, shaped
to the change-notification workflow's detection-output contract:
``event_id``, ``event_type``, ``regulation``, ``source_url``, ``old_hash``,
``new_hash``, ``detection_timestamp``. Downstream triage fields
(``triage_*``, ``status: pending``) are added by the workflow, not here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from regulatory_monitoring.fetchers import FetchFailure, fetch
from regulatory_monitoring.sources import Source, load_sources

STATE_FILENAME = "state.json"
CHANGELOG_FILENAME = "change-log.jsonl"


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class SourceOutcome:
    """What happened for one source in one run."""

    source_id: str
    status: str  # "unchanged" | "changed" | "new" | "failed"
    detail: str = ""


@dataclass
class RunSummary:
    """Aggregate result of one monitoring cycle."""

    started_at: datetime
    finished_at: datetime | None = None
    outcomes: list[SourceOutcome] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    @property
    def sources_checked(self) -> int:
        return len(self.outcomes)

    @property
    def sources_fetched(self) -> int:
        return sum(1 for o in self.outcomes if o.status != "failed")

    @property
    def failures(self) -> list[SourceOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    def summary_line(self) -> str:
        changed = sum(1 for o in self.outcomes if o.status in {"changed", "new"})
        return (
            f"regulatory-monitor: sources-checked={self.sources_checked} "
            f"sources-fetched={self.sources_fetched} changes={changed} "
            f"failures={len(self.failures)}"
        )


class StateStore:
    """Persist last-seen content hashes keyed by source id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Fail-loud: a corrupt state file must not masquerade as
            # "all new" or "no baseline". Refuse to run.
            raise RuntimeError(f"state file {self.path} is unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"state file {self.path} is not a JSON object")
        return data

    def save(self, state: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class ChangeLog:
    """Append-only JSONL change records."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, events: list[dict]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, sort_keys=True) + "\n")

    def next_seq(self, date_str: str) -> int:
        """Lowest unused sequence number for ``chng-<date_str>-NNN`` ids.

        The event id must be unique across the whole append-only log
        (4d workflow §6), and the documented operating mode runs several
        cycles per day — so the sequence continues from the highest id
        already persisted for that date rather than restarting per run.
        Malformed lines are ignored; this file's own integrity rules are
        enforced by the reader, not here.
        """
        if not self.path.exists():
            return 1
        prefix = f"chng-{date_str}-"
        highest = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_id = record.get("event_id", "") if isinstance(record, dict) else ""
            if isinstance(event_id, str) and event_id.startswith(prefix):
                suffix = event_id[len(prefix) :]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return highest + 1


def make_event_id(now: datetime, seq: int) -> str:
    """chng-YYYYMMDD-NNN per the 4d workflow schema."""
    return f"chng-{now:%Y%m%d}-{seq:03d}"


def run_cycle(
    base_dir: Path,
    sources: list[Source] | None = None,
    now: datetime | None = None,
    fetch_fn=fetch,
) -> RunSummary:
    """Execute one monitoring cycle.

    ``fetch_fn`` is injectable for tests (no live network). A source that
    fails to fetch keeps its previous hash — failure is recorded loudly in
    the run summary and never converted into "no changes" or a spurious
    change event.
    """
    sources = sources if sources is not None else load_sources()
    now = now or datetime.now(UTC)
    state_store = StateStore(base_dir / STATE_FILENAME)
    change_log = ChangeLog(base_dir / CHANGELOG_FILENAME)
    state = state_store.load()

    summary = RunSummary(started_at=now)
    events: list[dict] = []
    # Sequence continues from the highest id already persisted for this
    # date — two same-day cycles must never re-issue an event_id.
    seq = change_log.next_seq(f"{now:%Y%m%d}")

    for src in sources:
        try:
            result = fetch_fn(src.source_id, src.url, src.method)
        except FetchFailure as exc:
            summary.outcomes.append(SourceOutcome(src.source_id, "failed", str(exc)))
            continue
        new_hash = sha256_hex(result.content)
        old_hash = state.get(src.source_id)
        if old_hash is None:
            status, event_type = "new", "new_source"
        elif old_hash != new_hash:
            status, event_type = "changed", "amendment_detected"
        else:
            status, event_type = "unchanged", ""
        summary.outcomes.append(SourceOutcome(src.source_id, status))
        if status != "unchanged":
            events.append(
                {
                    "event_id": make_event_id(now, seq),
                    "event_type": event_type,
                    "regulation": src.regulation,
                    "source_id": src.source_id,
                    "source_url": result.url,
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "detection_timestamp": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "priority": src.priority,
                }
            )
            seq += 1
        state[src.source_id] = new_hash

    change_log.append(events)
    state_store.save(state)
    summary.events = events
    summary.finished_at = datetime.now(UTC)

    if summary.sources_checked == 0:
        raise RuntimeError(
            "regulatory-monitor: 0 sources configured — refusing to report a clean run"
        )
    if summary.sources_fetched == 0:
        raise RuntimeError(
            "regulatory-monitor: every source failed this run ("
            f"{summary.sources_checked} checked, 0 fetched) — monitoring is blind"
        )
    return summary
