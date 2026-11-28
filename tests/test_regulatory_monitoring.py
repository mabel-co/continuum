"""Regulatory monitoring pipeline — test suite.

Stdlib-only (unittest + tempfile + unittest.mock); no live network.
Covers the four mandated paths: change detection, no-change,
fetch-failure, and empty-run.

Run from the app root::

    PYTHONPATH=. python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from regulatory_monitoring import __main__ as cli
from regulatory_monitoring.fetchers import (
    FetchFailure,
    FetchResult,
    fetch_api,
    fetch_feed,
    fetch_scrape,
)
from regulatory_monitoring.pipeline import (
    RunSummary,
    StateStore,
    make_event_id,
    run_cycle,
    sha256_hex,
)
from regulatory_monitoring.sources import Source, load_sources

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _source(source_id: str = "test-feed", method: str = "feed") -> Source:
    return Source(
        source_id=source_id,
        name=f"Test source {source_id}",
        url=f"https://example.invalid/{source_id}",
        method=method,
        regulation="test-reg",
        cadence="daily",
        priority="high",
    )


def _rss(*items):
    body = "".join(f"<item><link>{u}</link><pubDate>{d}</pubDate></item>" for u, d in items)
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>' + body + "</channel></rss>"
    ).encode()


class FakeFetcher:
    """Injectable fetch_fn returning configured results/failures per source."""

    def __init__(self) -> None:
        self.responses: dict[str, bytes | Exception] = {}

    def __call__(self, source_id: str, url: str, method: str) -> FetchResult:
        response = self.responses[source_id]
        if isinstance(response, Exception):
            raise response
        return FetchResult(url=url, content=response)


class HashingTests(unittest.TestCase):
    def test_hash_is_stable_and_prefixed(self):
        self.assertEqual(sha256_hex(b"abc"), sha256_hex(b"abc"))
        self.assertTrue(sha256_hex(b"abc").startswith("sha256:"))
        self.assertNotEqual(sha256_hex(b"abc"), sha256_hex(b"abd"))

    def test_event_id_format(self):
        self.assertEqual(make_event_id(NOW, 7), "chng-20260920-007")


class FeedDigestTests(unittest.TestCase):
    """Feed normalisation: hashing must be order-insensitive and entry-based."""

    def test_reordering_is_not_a_change(self):
        with patch(
            "regulatory_monitoring.fetchers._http_get",
            return_value=(200, _rss(("u1", "d1"), ("u2", "d2")), ""),
        ):
            first = fetch_feed("s", "https://example.invalid/feed")
        with patch(
            "regulatory_monitoring.fetchers._http_get",
            return_value=(200, _rss(("u2", "d2"), ("u1", "d1")), ""),
        ):
            second = fetch_feed("s", "https://example.invalid/feed")
        self.assertEqual(first.content, second.content)

    def test_new_entry_changes_digest(self):
        with patch(
            "regulatory_monitoring.fetchers._http_get", return_value=(200, _rss(("u1", "d1")), "")
        ):
            first = fetch_feed("s", "https://example.invalid/feed")
        with patch(
            "regulatory_monitoring.fetchers._http_get",
            return_value=(200, _rss(("u1", "d1"), ("u2", "d2")), ""),
        ):
            second = fetch_feed("s", "https://example.invalid/feed")
        self.assertNotEqual(first.content, second.content)

    def test_html_masquerading_as_feed_fails_loud(self):
        rv = (200, b"<html><body>maintenance</body></html>", "")
        with (
            patch("regulatory_monitoring.fetchers._http_get", return_value=rv),
            self.assertRaises(FetchFailure),
        ):
            fetch_feed("s", "https://example.invalid/feed")

    def test_202_is_a_failure_not_a_clean_fetch(self):
        with (
            patch("regulatory_monitoring.fetchers._http_get", return_value=(202, b"", "")),
            self.assertRaises(FetchFailure),
        ):
            fetch_feed("s", "https://example.invalid/feed")

    def test_empty_feed_fails(self):
        with (
            patch("regulatory_monitoring.fetchers._http_get", return_value=(200, b"   ", "")),
            self.assertRaises(FetchFailure),
        ):
            fetch_feed("s", "https://example.invalid/feed")

    def test_entryless_feed_fails(self):
        with (
            patch("regulatory_monitoring.fetchers._http_get", return_value=(200, _rss(), "")),
            self.assertRaises(FetchFailure),
        ):
            fetch_feed("s", "https://example.invalid/feed")

    def test_http_error_is_failure(self):
        with (
            patch(
                "regulatory_monitoring.fetchers._http_get",
                side_effect=FetchFailure("s", "HTTP 403"),
            ),
            self.assertRaises(FetchFailure),
        ):
            fetch_feed("s", "https://example.invalid/feed")


class ApiDigestTests(unittest.TestCase):
    def test_normal_api_digest(self):
        body = json.dumps(
            {
                "results": [
                    {"document_number": "2026-001", "pubdate": "2026-09-01"},
                    {"document_number": "2026-002", "pubdate": "2026-09-02"},
                ]
            }
        ).encode()
        with patch(
            "regulatory_monitoring.fetchers._http_get", return_value=(200, body, "application/json")
        ):
            result = fetch_api("s", "https://example.invalid/api")
        self.assertIn(b"2026-001", result.content)

    def test_zero_results_fails_loud(self):
        body = json.dumps({"results": []}).encode()
        with (
            patch(
                "regulatory_monitoring.fetchers._http_get",
                return_value=(200, body, "application/json"),
            ),
            self.assertRaises(FetchFailure),
        ):
            fetch_api("s", "https://example.invalid/api")

    def test_non_json_fails(self):
        rv = (200, b"<html/>", "text/html")
        with (
            patch("regulatory_monitoring.fetchers._http_get", return_value=rv),
            self.assertRaises(FetchFailure),
        ):
            fetch_api("s", "https://example.invalid/api")


class ScrapeTests(unittest.TestCase):
    def test_empty_page_fails(self):
        rv = (200, b"", "text/html")
        with (
            patch("regulatory_monitoring.fetchers._http_get", return_value=rv),
            self.assertRaises(FetchFailure),
        ):
            fetch_scrape("s", "https://example.invalid")


class RunCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.sources = [_source("a"), _source("b")]
        self.fetcher = FakeFetcher()
        self.fetcher.responses = {"a": b"content-a-v1", "b": b"content-b-v1"}

    def _run(self, now=NOW):
        return run_cycle(self.base, sources=self.sources, now=now, fetch_fn=self.fetcher)

    def test_first_run_flags_all_sources_new_and_writes_log(self):
        summary = self._run()
        self.assertEqual({o.status for o in summary.outcomes}, {"new"})
        self.assertEqual(len(summary.events), 2)
        lines = (self.base / "change-log.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        record = json.loads(lines[0])
        for field in (
            "event_id",
            "event_type",
            "regulation",
            "source_url",
            "old_hash",
            "new_hash",
            "detection_timestamp",
        ):
            self.assertIn(field, record)
        self.assertEqual(record["event_type"], "new_source")
        self.assertIsNone(record["old_hash"])

    def test_second_run_no_change_writes_nothing(self):
        self._run()
        lines_before = len((self.base / "change-log.jsonl").read_text().splitlines())
        summary = self._run()
        self.assertEqual({o.status for o in summary.outcomes}, {"unchanged"})
        self.assertEqual(summary.events, [])
        lines_after = len((self.base / "change-log.jsonl").read_text().splitlines())
        self.assertEqual(lines_before, lines_after)

    def test_content_change_detected_with_old_and_new_hash(self):
        self._run()
        first_hash = json.loads((self.base / "state.json").read_text())["a"]
        self.fetcher.responses["a"] = b"content-a-v2"
        summary = self._run()
        changed = [e for e in summary.events if e["event_type"] == "amendment_detected"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["old_hash"], first_hash)
        self.assertNotEqual(changed[0]["new_hash"], first_hash)

    def test_fetch_failure_keeps_state_and_is_recorded_loudly(self):
        self._run()
        baseline = json.loads((self.base / "state.json").read_text())
        self.fetcher.responses["a"] = FetchFailure("a", "HTTP 503")
        summary = self._run()
        failed = [o for o in summary.outcomes if o.status == "failed"]
        self.assertEqual([o.source_id for o in failed], ["a"])
        self.assertIn("HTTP 503", failed[0].detail)
        # State must be preserved for the failed source, untouched.
        self.assertEqual(json.loads((self.base / "state.json").read_text())["a"], baseline["a"])
        # And a failure must never appear as a change event.
        self.assertEqual(summary.events, [])
        self.assertEqual(summary.sources_checked, 2)
        self.assertEqual(summary.sources_fetched, 1)

    def test_all_sources_failing_raises(self):
        self.fetcher.responses = {
            "a": FetchFailure("a", "HTTP 500"),
            "b": FetchFailure("b", "HTTP 500"),
        }
        with self.assertRaises(RuntimeError):
            self._run()

    def test_empty_source_list_raises(self):
        with self.assertRaises(RuntimeError):
            run_cycle(self.base, sources=[], now=NOW, fetch_fn=self.fetcher)

    def test_corrupt_state_refuses_to_run(self):
        (self.base / "state.json").write_text("{not json")
        with self.assertRaises(RuntimeError):
            self._run()

    def test_summary_line_counts(self):
        summary = self._run()
        self.assertIn("sources-checked=2 sources-fetched=2", summary.summary_line())

    def test_two_same_day_cycles_yield_distinct_event_ids(self):
        """4d §6 requires event_id uniqueness; the documented interim mode
        runs several cycles per day, so ids must never be re-issued."""
        first = self._run()
        self.fetcher.responses["a"] = b"content-a-v2"
        second = self._run()
        ids = [e["event_id"] for e in first.events + second.events]
        self.assertEqual(len(ids), len(set(ids)), f"event_id collision: {ids}")
        self.assertEqual(second.events[0]["event_id"], "chng-20260920-003")

    def test_event_id_sequence_survives_malformed_log_lines(self):
        (self.base / "change-log.jsonl").write_text(
            "not json\n" + json.dumps({"event_id": "chng-20260920-004"}) + "\n"
        )
        summary = self._run()
        ids = [e["event_id"] for e in summary.events]
        self.assertNotIn("chng-20260920-004", ids)
        self.assertEqual(len(ids), len(set(ids)))


class CliTests(unittest.TestCase):
    def _run_cli(self, fetcher, sources, base):
        def fake_run_cycle(data_dir, sources=None, **_kwargs):
            return run_cycle(data_dir, sources=sources, now=NOW, fetch_fn=fetcher)

        with (
            patch("regulatory_monitoring.__main__.load_sources", return_value=sources),
            patch("regulatory_monitoring.__main__.run_cycle", side_effect=fake_run_cycle),
            redirect_stdout(io.StringIO()),
        ):
            return cli.main(["--data-dir", str(base)])

    def test_exit_code_zero_when_clean(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fetcher = FakeFetcher()
        fetcher.responses = {"a": b"x"}
        code = self._run_cli(fetcher, [_source("a")], Path(tmp.name))
        self.assertEqual(code, 0)

    def test_exit_code_nonzero_on_partial_failure(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fetcher = FakeFetcher()
        fetcher.responses = {"a": FetchFailure("a", "boom"), "b": b"x"}
        code = self._run_cli(fetcher, [_source("a"), _source("b")], Path(tmp.name))
        self.assertEqual(code, 1)

    def test_exit_code_two_when_all_fail(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fetcher = FakeFetcher()
        fetcher.responses = {"a": FetchFailure("a", "boom")}
        code = self._run_cli(fetcher, [_source("a")], Path(tmp.name))
        self.assertEqual(code, 2)

    def test_cadence_flag_reaches_load_sources(self):
        """--cadence is parsed and passed to load_sources so a scheduled
        runner can run only the sources due at that timer."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen = {}

        def fake_sources(path=None, cadences=None):
            seen["cadences"] = cadences
            return []

        with (
            patch("regulatory_monitoring.__main__.load_sources", side_effect=fake_sources),
            patch("regulatory_monitoring.__main__.run_cycle") as runner,
            redirect_stdout(io.StringIO()),
        ):
            runner.return_value = RunSummary(
                started_at=NOW, finished_at=NOW, outcomes=[], events=[]
            )
            cli.main(["--data-dir", str(Path(tmp.name) / "d"), "--cadence", "daily, weekly"])
        self.assertEqual(seen["cadences"], ["daily", "weekly"])

    def test_empty_cadence_flag_means_all_sources(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen: dict[str, object] = {"cadences": "unset"}

        def fake_sources(path=None, cadences=None):
            seen["cadences"] = cadences
            return []

        with (
            patch("regulatory_monitoring.__main__.load_sources", side_effect=fake_sources),
            patch("regulatory_monitoring.__main__.run_cycle") as runner,
            redirect_stdout(io.StringIO()),
        ):
            runner.return_value = RunSummary(
                started_at=NOW, finished_at=NOW, outcomes=[], events=[]
            )
            cli.main(["--data-dir", str(Path(tmp.name) / "d")])
        self.assertIsNone(seen["cadences"])


class CadenceFilterTests(unittest.TestCase):
    """load_sources(cadences=...) — the scheduled vehicle's source selector."""

    def _write(self, entries):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sources.json"
        path.write_text(json.dumps({"sources": entries}))
        return path

    def _entry(self, source_id, cadence):
        return {
            "source_id": source_id,
            "name": source_id,
            "url": "https://example.invalid/" + source_id,
            "method": "scrape",
            "regulation": "r",
            "cadence": cadence,
            "priority": "low",
        }

    def test_none_returns_all(self):
        path = self._write([self._entry("a", "daily"), self._entry("b", "weekly")])
        self.assertEqual(len(load_sources(path, cadences=None)), 2)

    def test_filters_to_requested_cadences(self):
        path = self._write(
            [
                self._entry("a", "daily"),
                self._entry("b", "weekly"),
                self._entry("c", "monthly"),
            ]
        )
        got = {s.source_id for s in load_sources(path, cadences=["daily", "monthly"])}
        self.assertEqual(got, {"a", "c"})

    def test_cadence_match_is_case_insensitive_and_trims(self):
        path = self._write([self._entry("a", "Daily"), self._entry("b", "weekly")])
        got = {s.source_id for s in load_sources(path, cadences=[" DAILY ", ""])}
        self.assertEqual(got, {"a"})

    def test_checked_in_sources_cover_each_cadence(self):
        by_cadence = {c for c in ("daily", "weekly", "monthly")}
        cadences = {s.cadence for s in load_sources()}
        self.assertTrue(by_cadence <= cadences, "sources.json lost a cadence tier")


class SourceListTests(unittest.TestCase):
    def test_checked_in_sources_load_and_validate(self):
        sources = load_sources()
        self.assertGreaterEqual(len(sources), 5)
        ids = [s.source_id for s in sources]
        self.assertEqual(len(ids), len(set(ids)))
        for src in sources:
            self.assertIn(src.method, ("feed", "api", "scrape"))
            self.assertTrue(src.url.startswith("https://"))

    def test_source_missing_fields_raises(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sources.json"
        path.write_text(json.dumps({"sources": [{"source_id": "x"}]}))
        with self.assertRaises(TypeError):
            load_sources(path)

    def test_duplicate_source_ids_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sources.json"
        entry = {
            "source_id": "dup",
            "name": "n",
            "url": "https://example.invalid",
            "method": "scrape",
            "regulation": "r",
            "cadence": "daily",
            "priority": "low",
        }
        path.write_text(json.dumps({"sources": [entry, entry]}))
        with self.assertRaises(ValueError):
            load_sources(path)


class StateStoreTests(unittest.TestCase):
    def test_roundtrip(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = StateStore(Path(tmp.name) / "state.json")
        self.assertEqual(store.load(), {})
        store.save({"a": "sha256:1"})
        self.assertEqual(store.load(), {"a": "sha256:1"})


if __name__ == "__main__":
    unittest.main()
