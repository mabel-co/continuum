"""Tests for the HTTP entrypoint (__main__).

Stdlib unittest (NOT pytest), matching test_monitoring.py's style.
Discovered by ``python -m unittest discover -s tests`` from the app dir
with ``PYTHONPATH=.``, so ``import continuum_monitoring.__main__``
resolves.
"""

from __future__ import annotations

import socket
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from continuum_monitoring.__main__ import _Handler


class _TestServer:
    """Thin wrapper that starts/stops an HTTP server."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", self.port),
            _Handler,  # type: ignore[arg-type]
        )
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.1)

    def shutdown(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def _free_port() -> int:
    """Return an ephemeral TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


class TestEntrypoint(unittest.TestCase):
    """Integration tests for the __main__ HTTP server."""

    def setUp(self) -> None:
        self.port = _free_port()
        self.server = _TestServer(self.port)
        self.server.start()
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()

    def test_health(self) -> None:
        resp = urllib.request.urlopen(f"{self.base_url}/health")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"ok", resp.read())

    def test_dashboard(self) -> None:
        resp = urllib.request.urlopen(f"{self.base_url}/dashboard")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        body = resp.read().decode("utf-8")
        self.assertTrue(len(body) > 0)
        self.assertIn("<", body)
        # The entrypoint records one synthetic event before rendering, so the
        # dashboard must reflect real counters rather than the empty state.
        self.assertIn("health_probe", body)
        self.assertIn("events_total", body)

    def test_unknown_404(self) -> None:
        try:
            urllib.request.urlopen(f"{self.base_url}/bogus")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
        else:
            self.fail("Expected HTTPError 404")


if __name__ == "__main__":
    unittest.main()
