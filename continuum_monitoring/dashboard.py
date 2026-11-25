"""Continuum monitoring — internal operational dashboard (HTML).

Renders a dependency-free HTML page the team opens to see Continuum health:
overall counters, latency percentiles, per-operation breakdown, the configured
alert rules (the :data:`DEFAULT_RULES` when none are passed), and recent alert
history. This is the MVP dashboard — a static view of the in-process state,
not a Grafana deployment.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence

from continuum_monitoring.alerting import DEFAULT_RULES, AlertRule, AlertState
from continuum_monitoring.metrics import Metrics


def _table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def render_dashboard_html(
    metrics: Metrics,
    state: AlertState,
    rules: list[AlertRule] | None = None,
    title: str = "Continuum operational view",
) -> str:
    """Return a full HTML document (string) of the current operational view.

    Must include: the title; a counters table; latency percentiles; a
    per-operation table; the alert rules table (rule id + severity); and the
    recent alert history.
    """
    if rules is None:
        rules = DEFAULT_RULES
    counters = metrics.counters
    counters_rows = [
        (name, counters.get(name, "n/a"))
        for name in (
            "events_total",
            "events_success",
            "errors_total",
            "latency_samples",
            "tenants_seen",
        )
    ]
    if not counters:
        counters_rows.append(("no events yet", ""))
    latency_rows = [
        ("p50", "n/a" if metrics.latency_ms.p50 is None else f"{metrics.latency_ms.p50:.1f} ms"),
        ("p95", "n/a" if metrics.latency_ms.p95 is None else f"{metrics.latency_ms.p95:.1f} ms"),
    ]
    per_operation_rows = [
        (op, counts["success"], counts["error"]) for op, counts in metrics.per_operation.items()
    ] or [("n/a", 0, 0)]
    rules_rows = [(r.rule_id, r.severity, r.description) for r in rules] or [("n/a", "", "")]
    history_rows = [
        (a.get("rule_id", ""), a.get("severity", ""), a.get("ts", "")) for a in state.history
    ]
    history = _table(("rule", "severity", "time"), history_rows) if history_rows else "<p>none</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; color: #222; }}
h1 {{ font-size: 1.4rem; }}
h2 {{ font-size: 1.1rem; margin-top: 1.5rem; }}
table {{ border-collapse: collapse; margin: 0.5rem 0; }}
th, td {{ border: 1px solid #999; padding: 0.3rem 0.6rem; text-align: left; }}
th {{ background: #eee; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<h2>Counters</h2>
{_table(("name", "value"), counters_rows)}
<h2>Latency</h2>
{_table(("percentile", "value"), latency_rows)}
<h2>Per operation</h2>
{_table(("operation", "success", "error"), per_operation_rows)}
<h2>Alert rules</h2>
{_table(("rule", "severity", "description"), rules_rows)}
<h2>Recent alerts</h2>
{history}
</body>
</html>
"""
