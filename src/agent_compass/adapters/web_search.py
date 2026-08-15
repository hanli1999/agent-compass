"""Built-in web search adapters.

Two backends ship by default so a host can reach the open web without
writing its own client:

* :class:`DuckDuckGoAdapter` — HTML scrape of ``duckduckgo.com/html/``. No
  API key, no SDK, only ``urllib`` from the standard library. Used as the
  default. Results are best-effort: the page layout is unofficial, so the
  parser is intentionally lenient (title, snippet, URL) and any row that
  does not parse is skipped rather than aborting the call.
* :class:`TavilyAdapter` — JSON API at ``api.tavily.com``. Requires the
  ``TAVILY_API_KEY`` environment variable. Recommended when the caller
  needs stable, low-variance output for a ReAct loop.

Both adapters implement the :class:`~agent_compass.retrieval.Retriever`
protocol directly: they return :class:`RetrievedItem` summaries (never
full page bodies), honour ``CompassConfig.remote_allowed``, apply the
privacy boundary to every response, and time out fast so a flaky
network does not stall the host.

The default core stays dependency-free, so anything heavier than
``urllib`` + ``re`` + ``json`` would be the wrong direction here.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

from ..config import CompassConfig
from ..privacy.boundary import PrivacyBoundary
from ..retrieval.models import RetrievalQuery, RetrievedItem
from ..retrieval.summarize import DEFAULT_SUMMARY_CHARS, summarize


class RemoteNotAllowedError(RuntimeError):
    """Raised when the adapter is asked to hit the network without permission.

    The orchestrator catches this and records it as a per-source error so
    a missing ``remote_allowed`` flag never takes down local recall.
    """


class WebAdapterError(RuntimeError):
    """Raised when the network call itself fails after retries.

    The orchestrator records the message and moves on. The agent should
    treat the source as "no answer" rather than "the call crashed".
    """


# ----- shared helpers ------------------------------------------------------


def _build_boundary(config: CompassConfig | None) -> PrivacyBoundary:
    # The web adapters need a PrivacyBoundary regardless of who owns the
    # rest of the system. We construct a fresh one with the bundled
    # detector; if a host wants its own rules it can wrap the adapter.
    return PrivacyBoundary()


def _http_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout_s: float,
    retries: int,
) -> bytes:
    """GET with a single retry on transient failures. No real backoff."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                break
    raise WebAdapterError(f"GET {url} failed after {retries + 1} attempt(s): {last_error!r}")


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_s: float,
    retries: int,
) -> bytes:
    last_error: Exception | None = None
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                break
    raise WebAdapterError(f"POST {url} failed after {retries + 1} attempt(s): {last_error!r}")


def _redact_html(text: str, boundary: PrivacyBoundary) -> str:
    """Run the privacy boundary over a chunk of text fetched from the web.

    The bundled detector catches the obvious cases (emails, IPs, paths,
    credit cards, mainland China phones/IDs). Anything more specific is
    the host's problem — this is a baseline, not a complete DLP product.
    """
    try:
        return boundary.assert_safe_for_remote(text)
    except ValueError:
        # A secret landed in the response. The contract is "block, not pass
        # through". We do not return a partial redaction; the orchestrator
        # will record this row as an error and skip it.
        raise


def _to_items(
    *,
    rows: Iterable[dict[str, Any]],
    source: str,
    keywords: list[str],
    boundary: PrivacyBoundary,
    summary_chars: int,
) -> list[RetrievedItem]:
    items: list[RetrievedItem] = []
    for index, row in enumerate(rows):
        title = (row.get("title") or "").strip()
        snippet = (row.get("snippet") or row.get("content") or "").strip()
        url = (row.get("url") or row.get("href") or "").strip()
        if not url:
            continue
        try:
            safe_snippet = _redact_html(snippet, boundary)
            safe_title = _redact_html(title, boundary)
        except ValueError:
            # A secret showed up in the snippet. Skip the row, not the call.
            continue
        summary, truncated = summarize(
            f"{safe_title}\n\n{safe_snippet}".strip() if safe_title else safe_snippet,
            keywords=keywords,
            max_chars=summary_chars,
        )
        items.append(
            RetrievedItem(
                memory_id=f"{source}:{index}:{urllib.parse.quote(url, safe=':/')}",
                summary=summary,
                score=float(row.get("score", 0.5) or 0.5),
                memory_type="web_search",
                source=source,
                keyword_hits=_count_hits(f"{safe_title} {safe_snippet}", keywords),
                age_days=0.0,
                formula_version="external",
                truncated=truncated,
            )
        )
    return items


def _count_hits(text: str, keywords: list[str]) -> int:
    if not keywords:
        return 0
    lowered = text.lower()
    return sum(1 for k in keywords if k in lowered)


# ----- DuckDuckGo ----------------------------------------------------------


#: A pragmatic, leniency-first HTML parser. DDG's "html" endpoint is
#: unofficial and changes shape. We pull every ``<a class="result__a">``
#: we can find, then read the surrounding ``result__snippet`` if present.
_DDG_RESULT_A = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_RESULT_SNIPPET = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIPPER = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    cleaned = _TAG_STRIPPER.sub(" ", text)
    return _WHITESPACE.sub(" ", cleaned).strip()


class DuckDuckGoAdapter:
    """Web search via DuckDuckGo's HTML endpoint. No API key.

    The endpoint sometimes returns a captcha / 202 page when it sees too
    much traffic. When that happens we surface :class:`WebAdapterError`
    and the orchestrator records the source as failed. The host can
    fall back to local memory or a different backend.
    """

    name = "web_search_ddg"

    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        config: CompassConfig | None = None,
        *,
        user_agent: str = "agent-compass/0.7 (+https://github.com/hanli1999/agent-compass)",
        boundary: PrivacyBoundary | None = None,
    ):
        self.config = config or CompassConfig.from_env()
        self._user_agent = user_agent
        self._boundary = boundary or _build_boundary(self.config)

    def _guard_remote(self) -> None:
        if not self.config.remote_allowed:
            raise RemoteNotAllowedError(
                "DuckDuckGoAdapter requires CompassConfig.remote_allowed=True"
            )

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedItem]:
        self._guard_remote()
        params = urllib.parse.urlencode({"q": query.text, "kl": "us-en"})
        url = f"{self.ENDPOINT}?{params}"
        try:
            body = _http_get(
                url,
                headers={"User-Agent": self._user_agent, "Accept": "text/html"},
                timeout_s=self.config.web_search_timeout_s,
                retries=self.config.web_retries,
            ).decode("utf-8", errors="replace")
        except WebAdapterError:
            raise

        rows: list[dict[str, Any]] = []
        for match in _DDG_RESULT_A.finditer(body):
            href = urllib.parse.unquote(match.group(1))
            title = _strip_html(match.group(2))
            snippet = _strip_html(_DDG_RESULT_SNIPPET.search(body, match.end()).group(1)) \
                if _DDG_RESULT_SNIPPET.search(body, match.end()) else ""
            rows.append({"title": title, "snippet": snippet, "url": href, "score": 0.5})
        if not rows:
            # Be honest: if the parser saw nothing, treat it as a soft error
            # so the orchestrator records it instead of pretending success.
            raise WebAdapterError("duckduckgo returned no parseable results")

        return _to_items(
            rows=rows,
            source=self.name,
            keywords=query.effective_keywords(),
            boundary=self._boundary,
            summary_chars=DEFAULT_SUMMARY_CHARS,
        )


# ----- Tavily --------------------------------------------------------------


class TavilyAdapter:
    """Web search via the Tavily JSON API. Requires ``TAVILY_API_KEY``.

    The adapter asks Tavily for ``max_results`` short results, redacts
    each snippet, and returns them as :class:`RetrievedItem` objects
    the same way :class:`DuckDuckGoAdapter` does. The two backends are
    intentionally interchangeable: the orchestrator does not need to
    know which one fired.
    """

    name = "web_search_tavily"

    ENDPOINT = "https://api.tavily.com/search"

    def __init__(
        self,
        config: CompassConfig | None = None,
        *,
        api_key: str | None = None,
        max_results: int = 7,
        boundary: PrivacyBoundary | None = None,
    ):
        self.config = config or CompassConfig.from_env()
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self._max_results = max(1, int(max_results))
        self._boundary = boundary or _build_boundary(self.config)

    def _guard_remote(self) -> None:
        if not self.config.remote_allowed:
            raise RemoteNotAllowedError(
                "TavilyAdapter requires CompassConfig.remote_allowed=True"
            )
        if not self._api_key:
            raise WebAdapterError(
                "TavilyAdapter requires TAVILY_API_KEY (env or api_key=...)"
            )

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedItem]:
        self._guard_remote()
        payload = {
            "api_key": self._api_key,
            "query": query.text,
            "max_results": self._max_results,
            "include_answer": False,
        }
        try:
            body = _http_post_json(
                self.ENDPOINT,
                payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout_s=self.config.web_search_timeout_s,
                retries=self.config.web_retries,
            )
        except WebAdapterError:
            raise

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise WebAdapterError(f"tavily returned invalid JSON: {exc}") from exc

        rows: list[dict[str, Any]] = []
        for entry in parsed.get("results", []) or []:
            rows.append(
                {
                    "title": entry.get("title", ""),
                    "snippet": entry.get("content", ""),
                    "url": entry.get("url", ""),
                    "score": float(entry.get("score", 0.5) or 0.5),
                }
            )
        if not rows:
            raise WebAdapterError("tavily returned no results")

        return _to_items(
            rows=rows,
            source=self.name,
            keywords=query.effective_keywords(),
            boundary=self._boundary,
            summary_chars=DEFAULT_SUMMARY_CHARS,
        )


__all__ = [
    "DuckDuckGoAdapter",
    "RemoteNotAllowedError",
    "TavilyAdapter",
    "WebAdapterError",
]
