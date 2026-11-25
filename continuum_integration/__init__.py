"""Continuum integration — regulatory change notification engine.

Implements Step 5 (Product Notification) of the regulatory change workflow:
publishes activated change events to an append-only JSONL notification bus
and fans them out to downstream consumers (dashboard, compliance alerting,
report generator, evidence collection / mapping engine).

Stdlib-only (no external dependencies).
"""

from __future__ import annotations

__all__ = ["models", "publisher", "bus", "fanout"]
