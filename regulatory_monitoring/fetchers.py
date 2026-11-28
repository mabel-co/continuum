"""HTTP fetchers for the three detection methods (feed / api / scrape).

A fetcher returns a ``FetchResult`` with normalised content bytes, or
raises ``FetchFailure``. Silence is never an option: transport errors,
unexpected status codes, and non-parseable payloads are all failures
(fail-loud requirement).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

USER_AGENT = "ContinuumRegulatoryMonitor/1.0 (+https://github.com/mabel-co/continuum)"
DEFAULT_TIMEOUT = 30

# Statuses that mean "content unchanged, request accepted for processing"
# rather than an error (EUR-Lex returns 202 on its async feed pipeline).
RETRYABLE_STATUSES = frozenset({202})

_XHTML_TAG = re.compile(rb"<\s*(?:!DOCTYPE\s+html|html\b)", re.IGNORECASE)


class FetchFailure(Exception):
    """A source could not be fetched or its payload was unusable."""

    def __init__(self, source_id: str, reason: str) -> None:
        super().__init__(f"{source_id}: {reason}")
        self.source_id = source_id
        self.reason = reason


@dataclass(frozen=True)
class FetchResult:
    """Normalised bytes for one source plus the URL that served them."""

    url: str
    content: bytes


def _http_get(url: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise FetchFailure(url, f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FetchFailure(url, f"transport error: {reason}") from exc


def _looks_like_html(data: bytes) -> bool:
    return bool(_XHTML_TAG.search(data[:2048]))


def fetch_feed(source_id: str, url: str, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
    """Fetch an RSS/Atom feed and return a normalised digest of its entries.

    The hashed content is the sorted set of (id/link, updated/published)
    pairs — not the raw XML — so feed-server reordering or cosmetic
    changes do not masquerade as regulatory change. A non-XML (e.g. HTML
    maintenance) payload is a failure, never a silent empty change set.
    """
    status, data, _ctype = _http_get(url, timeout)
    if status in RETRYABLE_STATUSES:
        # Feed accepted for processing; no content this cycle. Raise so the
        # run summary records it rather than logging "no changes".
        raise FetchFailure(
            source_id, f"HTTP {status} (feed accepted for processing, retry next cycle)"
        )
    if not data.strip():
        raise FetchFailure(source_id, "empty feed body")
    if _looks_like_html(data):
        raise FetchFailure(source_id, "feed URL served HTML, not XML")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FetchFailure(source_id, f"unparseable feed XML: {exc}") from exc
    entries: list[str] = []
    for entry in root.iter():
        tag = entry.tag.rpartition("}")[2]
        if tag not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for child in entry:
            ctag = child.tag.rpartition("}")[2]
            if ctag in {"id", "link", "published", "updated", "pubDate", "lastBuildDate"}:
                href = child.get("href")
                value = (href or (child.text or "")).strip()
                fields.setdefault(ctag, value)
        key = fields.get("id") or fields.get("link") or ""
        stamp = fields.get("updated") or fields.get("published") or fields.get("pubDate") or ""
        entries.append(f"{key}|{stamp}")
    if not entries:
        raise FetchFailure(source_id, "feed parsed but contained no entries")
    digest = "\n".join(sorted(entries)).encode("utf-8")
    return FetchResult(url=url, content=digest)


def fetch_api(source_id: str, url: str, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
    """Fetch a JSON API listing and return a normalised digest of documents."""
    status, data, _ctype = _http_get(url, timeout)
    if status in RETRYABLE_STATUSES:
        raise FetchFailure(source_id, f"HTTP {status} (accepted for processing, retry next cycle)")
    if not data.strip():
        raise FetchFailure(source_id, "empty API body")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchFailure(source_id, f"API returned non-JSON: {exc}") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise FetchFailure(source_id, "API response missing 'results' list")
    if not results:
        raise FetchFailure(
            source_id, "API returned zero results (unexpected for newest-first query)"
        )
    keys: list[str] = []
    for doc in results:
        if not isinstance(doc, dict):
            raise FetchFailure(source_id, "API result entry is not an object")
        key = f"{doc.get('document_number', doc.get('api_url', ''))}|{doc.get('pubdate', '')}"
        keys.append(key)
    return FetchResult(url=url, content="\n".join(sorted(keys)).encode("utf-8"))


def fetch_scrape(source_id: str, url: str, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
    """Fetch an HTML page for whole-page hash comparison."""
    status, data, _ctype = _http_get(url, timeout)
    if status in RETRYABLE_STATUSES:
        raise FetchFailure(source_id, f"HTTP {status} (accepted for processing, retry next cycle)")
    if not data.strip():
        raise FetchFailure(source_id, "empty page body")
    return FetchResult(url=url, content=data)


FETCHERS = {"feed": fetch_feed, "api": fetch_api, "scrape": fetch_scrape}


def fetch(source_id: str, url: str, method: str, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
    """Dispatch to the fetcher for a source's detection method."""
    try:
        fetcher = FETCHERS[method]
    except KeyError:
        raise FetchFailure(source_id, f"unknown detection method {method!r}") from None
    return fetcher(source_id, url, timeout)
