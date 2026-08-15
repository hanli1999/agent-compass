"""Built-in web page fetcher.

:class:`WebFetchAdapter` turns a URL into a :class:`RetrievedItem`
summary the same way the search adapters do. It is deliberately
minimal:

* Only :mod:`urllib` from the standard library.
* Conservative HTML extraction — strip tags, collapse whitespace, take
  the first ``max_chars`` characters. No JS execution, no DOM
  rebuilding. A page that needs JavaScript to render is not what this
  adapter is for.
* The privacy boundary is applied to the raw text before
  summarisation. Any secret that slips through the bundled detector
  causes the whole row to be dropped, not silently passed on.
* The HTTP client uses ``HEAD`` on the URL to confirm it points at an
  HTML resource before downloading. ``HEAD`` failures are *not*
  retried; the orchestrator records them as a per-source error and
  the host can fall back to a different URL.

The adapter implements :class:`~agent_compass.retrieval.Retriever`,
so it slots into :class:`RetrievalOrchestrator` without an extra
wrapper — the same way :class:`CallableRetriever` does. Hosts that
want a richer extractor (readability, Markdown conversion, structured
data) should pass their own callable via :class:`CallableRetriever`
instead of extending this one.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import re
from typing import Any

from ..config import CompassConfig
from ..privacy.boundary import PrivacyBoundary
from ..retrieval.models import RetrievalQuery, RetrievedItem
from ..retrieval.summarize import DEFAULT_SUMMARY_CHARS, summarize
from .web_search import (
    RemoteNotAllowedError,
    WebAdapterError,
    _build_boundary,
    _count_hits,
    _http_get,
)


_TAG_STRIPPER = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_SCRIPT_BLOCK = re.compile(r"<script\b.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK = re.compile(r"<style\b.*?</style>", re.IGNORECASE | re.DOTALL)
_META_DESCRIPTION = re.compile(
    r'<meta\s+name="description"\s+content="([^"]*)"',
    re.IGNORECASE,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _strip_html(text: str) -> str:
    text = _SCRIPT_BLOCK.sub(" ", text)
    text = _STYLE_BLOCK.sub(" ", text)
    text = _TAG_STRIPPER.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


class WebFetchAdapter:
    """Fetch a single URL and return its text as a :class:`RetrievedItem`."""

    name = "web_fetch"

    def __init__(
        self,
        config: CompassConfig | None = None,
        *,
        user_agent: str = "agent-compass/0.7 (+https://github.com/hanli1999/agent-compass)",
        max_chars: int = 4000,
        boundary: PrivacyBoundary | None = None,
    ):
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1")
        self.config = config or CompassConfig.from_env()
        self._user_agent = user_agent
        self._max_chars = max_chars
        self._boundary = boundary or _build_boundary(self.config)

    def _guard_remote(self) -> None:
        if not self.config.remote_allowed:
            raise RemoteNotAllowedError(
                "WebFetchAdapter requires CompassConfig.remote_allowed=True"
            )

    def _extract_text(self, html: str) -> tuple[str, str]:
        """Return ``(title, body)`` from an HTML document."""
        title_match = _TITLE.search(html)
        title = _strip_html(title_match.group(1)) if title_match else ""
        description_match = _META_DESCRIPTION.search(html)
        description = ""
        if description_match:
            description = _strip_html(description_match.group(1))
        body = _strip_html(html)
        # Prefer the meta description if it covers what the body would
        # produce; otherwise use the stripped body. Meta descriptions are
        # written by humans to summarise the page, so they're a better
        # seed for the summary than the first 4000 chars of HTML.
        seed = description or body
        return title, seed[: self._max_chars]

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedItem]:
        # ``WebFetchAdapter`` is "look at this URL, summarise it". The
        # ``query.text`` is the URL, and ``query.keywords`` are what
        # the caller wants the summary to highlight. Anything else
        # (memory_type, since_days) is ignored on purpose.
        url = (query.text or "").strip()
        if not url:
            raise WebAdapterError("web_fetch needs a non-empty query.text URL")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WebAdapterError(f"web_fetch only accepts http(s) URLs, got: {url!r}")

        self._guard_remote()

        try:
            body = _http_get(
                url,
                headers={"User-Agent": self._user_agent, "Accept": "text/html,text/plain"},
                timeout_s=self.config.web_fetch_timeout_s,
                retries=self.config.web_retries,
            ).decode("utf-8", errors="replace")
        except WebAdapterError:
            raise

        title, text = self._extract_text(body)
        try:
            safe_text = self._boundary.assert_safe_for_remote(text)
        except ValueError as exc:
            raise WebAdapterError(f"web_fetch response contained a secret: {exc}") from exc
        try:
            safe_title = self._boundary.assert_safe_for_remote(title)
        except ValueError as exc:
            raise WebAdapterError(f"web_fetch title contained a secret: {exc}") from exc

        keywords = query.effective_keywords()
        summary, truncated = summarize(
            f"{safe_title}\n\n{safe_text}".strip() if safe_title else safe_text,
            keywords=keywords,
            max_chars=DEFAULT_SUMMARY_CHARS,
        )
        return [
            RetrievedItem(
                memory_id=f"web_fetch:{urllib.parse.quote(url, safe=':/')}",
                summary=summary,
                score=1.0,
                memory_type="web_page",
                source=self.name,
                keyword_hits=_count_hits(f"{safe_title} {safe_text}", keywords),
                age_days=0.0,
                formula_version="external",
                truncated=truncated,
            )
        ]


__all__ = ["WebFetchAdapter"]
