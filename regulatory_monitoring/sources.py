"""Machine-readable source list for the regulatory monitoring pipeline.

Curated and verified against the public sources themselves. Each entry
carries: source id, name, URL, regulation, cadence, and detection method
(feed / scrape / api).

Sources listed ``manual`` in the catalog (EUR-Lex Alert email, JS-rendered
pages that no HTTP fetch can read) are recorded in the catalog document
only — this list contains exclusively machine-checkable sources, so that
anything absent here is, by definition, outside automated monitoring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SOURCES_PATH = Path(__file__).parent / "sources.json"

VALID_METHODS = ("feed", "api", "scrape")


@dataclass(frozen=True)
class Source:
    """One monitorable regulatory source."""

    source_id: str
    name: str
    url: str
    method: str  # feed | api | scrape
    regulation: str
    cadence: str
    priority: str
    notes: str = ""


def load_sources(
    path: Path | str = SOURCES_PATH, cadences: list[str] | None = None
) -> list[Source]:
    """Load and validate the checked-in source list.

    ``cadences`` optionally restricts the set to sources whose ``cadence``
    field is in the list (e.g. ``["daily"]``). A scheduled runner uses
    this to run only the sources due at a given time.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = [Source(**entry) for entry in raw["sources"]]
    if cadences is not None:
        wanted = {c.strip().lower() for c in cadences if c.strip()}
        sources = [s for s in sources if s.cadence.lower() in wanted]
    seen: set[str] = set()
    for src in sources:
        if src.source_id in seen:
            raise ValueError(f"duplicate source_id in sources.json: {src.source_id}")
        seen.add(src.source_id)
        if src.method not in VALID_METHODS:
            raise ValueError(f"source {src.source_id}: invalid method {src.method!r}")
    return sources
