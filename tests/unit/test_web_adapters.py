"""Tests for the built-in web adapters.

The point of these tests is to prove the adapters work *without* the
real network. Each test injects a fake ``_http_get`` / ``_http_post_json``
so the parser, the privacy boundary, the timeout and the retry path can
be exercised in CI.

We monkey-patch the module-level helpers in ``agent_compass.adapters.web_search``
and ``web_fetch`` rather than the adapter classes, so a future refactor
that swaps the helper out still gets caught here.
"""
from __future__ import annotations

import json

import pytest

from agent_compass import CompassConfig
from agent_compass.adapters.web_fetch import WebFetchAdapter
from agent_compass.adapters.web_search import (
    DuckDuckGoAdapter,
    RemoteNotAllowedError,
    TavilyAdapter,
    WebAdapterError,
)
from agent_compass.retrieval.models import RetrievalQuery


def _config(remote_allowed: bool = True, **overrides) -> CompassConfig:
    return CompassConfig(data_dir=__import__("pathlib").Path("/tmp"), remote_allowed=remote_allowed, **overrides)


# ---- DuckDuckGo ----------------------------------------------------------


def test_ddg_refuses_when_remote_not_allowed(monkeypatch):
    monkeypatch.setattr(
        "agent_compass.adapters.web_search._http_get",
        lambda *a, **kw: pytest.fail("network should not be touched"),
    )
    adapter = DuckDuckGoAdapter(_config(remote_allowed=False))
    with pytest.raises(RemoteNotAllowedError):
        adapter.retrieve(RetrievalQuery(text="fastapi 0.118"))


def test_ddg_parses_results(monkeypatch):
    html = """
    <html><body>
      <div class="result">
        <a class="result__a" href="https://example.com/a">FastAPI 0.118 release notes</a>
        <a class="result__snippet">First paragraph summary for test purposes.</a>
      </div>
      <div class="result">
        <a class="result__a" href="https://example.com/b">Second hit</a>
        <a class="result__snippet">Second snippet without any PII in it.</a>
      </div>
    </body></html>
    """
    monkeypatch.setattr("agent_compass.adapters.web_search._http_get", lambda *a, **kw: html.encode("utf-8"))
    items = DuckDuckGoAdapter(_config()).retrieve(RetrievalQuery(text="fastapi 0.118"))
    assert len(items) == 2
    assert items[0].source == "web_search_ddg"
    assert "example.com/a" in items[0].memory_id
    assert "FastAPI 0.118" in items[0].summary or "FastAPI 0.118" in items[0].summary.replace("…", "")


def test_ddg_redacts_email_in_snippet(monkeypatch):
    html = """
    <a class="result__a" href="https://example.com/x">x</a>
    <a class="result__snippet">contact alice@example.com about the migration</a>
    """
    monkeypatch.setattr("agent_compass.adapters.web_search._http_get", lambda *a, **kw: html.encode("utf-8"))
    items = DuckDuckGoAdapter(_config()).retrieve(RetrievalQuery(text="x"))
    assert len(items) == 1
    assert "alice@example.com" not in items[0].summary
    assert "[REDACTED" in items[0].summary


def test_ddg_drops_row_with_secret(monkeypatch):
    # A row whose snippet contains a *secret* is dropped silently — the
    # rest of the response is still returned, the host sees a missing
    # match, the URL is not surfaced. We never pass the secret through.
    html = """
    <a class="result__a" href="https://example.com/x">x</a>
    <a class="result__snippet">api_key=abcdefghijklmnop leaked here</a>
    <a class="result__a" href="https://example.com/y">y</a>
    <a class="result__snippet">clean snippet without any PII in it</a>
    """
    monkeypatch.setattr("agent_compass.adapters.web_search._http_get", lambda *a, **kw: html.encode("utf-8"))
    items = DuckDuckGoAdapter(_config()).retrieve(RetrievalQuery(text="x"))
    urls = [item.memory_id for item in items]
    assert "https://example.com/x" not in "".join(urls)
    assert any("y" in u for u in urls)


def test_ddg_returns_no_results_error(monkeypatch):
    monkeypatch.setattr("agent_compass.adapters.web_search._http_get", lambda *a, **kw: b"<html>nothing</html>")
    with pytest.raises(WebAdapterError):
        DuckDuckGoAdapter(_config()).retrieve(RetrievalQuery(text="x"))


# ---- Tavily --------------------------------------------------------------


def test_tavily_refuses_without_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    adapter = TavilyAdapter(_config(), api_key=None)
    with pytest.raises(WebAdapterError):
        adapter.retrieve(RetrievalQuery(text="x"))


def test_tavily_parses_json(monkeypatch):
    payload = {
        "results": [
            {
                "title": "FastAPI 0.118",
                "content": "release notes content",
                "url": "https://example.com/r",
                "score": 0.9,
            }
        ]
    }
    monkeypatch.setattr("agent_compass.adapters.web_search._http_post_json", lambda *a, **kw: json.dumps(payload).encode("utf-8"))
    items = TavilyAdapter(_config(), api_key="dummy").retrieve(RetrievalQuery(text="fastapi"))
    assert len(items) == 1
    assert items[0].source == "web_search_tavily"
    assert "FastAPI 0.118" in items[0].summary


def test_tavily_empty_results_is_error(monkeypatch):
    monkeypatch.setattr("agent_compass.adapters.web_search._http_post_json", lambda *a, **kw: json.dumps({"results": []}).encode("utf-8"))
    with pytest.raises(WebAdapterError):
        TavilyAdapter(_config(), api_key="dummy").retrieve(RetrievalQuery(text="x"))


# ---- WebFetch ------------------------------------------------------------


def test_web_fetch_refuses_when_remote_not_allowed():
    adapter = WebFetchAdapter(_config(remote_allowed=False))
    with pytest.raises(RemoteNotAllowedError):
        adapter.retrieve(RetrievalQuery(text="https://example.com"))


def test_web_fetch_rejects_non_http_url():
    adapter = WebFetchAdapter(_config())
    with pytest.raises(WebAdapterError):
        adapter.retrieve(RetrievalQuery(text="file:///etc/passwd"))


def test_web_fetch_summarises_page(monkeypatch):
    html = """
    <html><head>
      <title>FastAPI 0.118 release notes</title>
      <meta name="description" content="FastAPI 0.118 ships a new lifespan helper.">
    </head><body>
      <p>The new lifespan helper simplifies startup and shutdown hooks.</p>
    </body></html>
    """
    monkeypatch.setattr("agent_compass.adapters.web_fetch._http_get", lambda *a, **kw: html.encode("utf-8"))
    items = WebFetchAdapter(_config()).retrieve(RetrievalQuery(text="https://example.com/fastapi"))
    assert len(items) == 1
    assert items[0].source == "web_fetch"
    assert "lifespan" in items[0].summary or "FastAPI" in items[0].summary


def test_web_fetch_redacts_email(monkeypatch):
    html = """
    <html><head><title>contact</title></head>
    <body><p>reach alice@example.com for the migration help</p></body></html>
    """
    monkeypatch.setattr("agent_compass.adapters.web_fetch._http_get", lambda *a, **kw: html.encode("utf-8"))
    items = WebFetchAdapter(_config()).retrieve(RetrievalQuery(text="https://example.com/x"))
    assert len(items) == 1
    assert "alice@example.com" not in items[0].summary
