"""Continuum monitoring — HTTP entrypoint (stdlib only).

Provides a lightweight HTTP server on a configurable port with
``/health`` and ``/dashboard`` endpoints so the monitoring package can
run as a Docker Compose service without third-party dependencies.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from continuum_monitoring.alerting import AlertState
from continuum_monitoring.dashboard import render_dashboard_html
from continuum_monitoring.metrics import Metrics, new_event


class _Handler(BaseHTTPRequestHandler):
    """Handle ``/health`` and ``/dashboard`` requests."""

    def do_GET(self: _Handler) -> None:  # type: ignore[override]
        if self.path == "/health":
            body = json.dumps({"status": "ok"})
            self._send(200, "application/json", body)
        elif self.path == "/dashboard":
            metrics = Metrics()
            metrics.record(
                new_event(
                    operation="health_probe",
                    status="success",
                    tenant_id="monitoring",
                )
            )
            html = render_dashboard_html(metrics, AlertState())
            self._send(200, "text/html", html)
        else:
            body = json.dumps({"error": "not found"})
            self._send(404, "application/json", body)

    def _send(self, status: int, content_type: str, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self: _Handler, fmt: str, *args: object) -> None:
        """Suppress per-request log lines in production."""
        pass


def main() -> None:
    port = int(os.environ.get("MONITORING_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    print(f"Listening on 0.0.0.0:{port}")  # noqa: T201
    server.serve_forever()


if __name__ == "__main__":
    main()
