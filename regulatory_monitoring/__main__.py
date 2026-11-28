"""CLI entrypoint: run one regulatory monitoring cycle.

Usage::

    python -m regulatory_monitoring [--data-dir PATH] [--sources PATH]

Exits non-zero on any fetch failure (fail-loud) after writing the run's
durable records, so a cron/scheduler or operator sees the failure in the
run result, not just the log.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from regulatory_monitoring.pipeline import run_cycle
from regulatory_monitoring.sources import SOURCES_PATH, load_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regulatory-monitor", description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data",
        help="Directory for state.json and change-log.jsonl "
        "(default: ./data next to the package)",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="Override sources.json path (default: package sources.json)",
    )
    parser.add_argument(
        "--cadence",
        default="",
        help="Comma-separated cadences to run this cycle (e.g. 'daily' "
        "or 'daily,weekly'); empty = all sources. Used by a scheduled "
        "runner so each timer runs only the sources due.",
    )
    args = parser.parse_args(argv)

    cadences = [c for c in (p.strip() for p in args.cadence.split(",")) if c] or None
    sources = load_sources(args.sources or SOURCES_PATH, cadences=cadences)
    try:
        summary = run_cycle(args.data_dir, sources=sources)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for outcome in summary.outcomes:
        marker = {"failed": "FAIL", "changed": "CHANGED", "new": "NEW", "unchanged": "ok"}[
            outcome.status
        ]
        line = f"  [{marker:>7}] {outcome.source_id}"
        if outcome.detail:
            line += f" — {outcome.detail}"
        print(line)
    print(summary.summary_line())
    for event in summary.events:
        print(f"  event {event['event_id']}: {event['event_type']} on {event['source_id']}")
    return 1 if summary.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
