"""Adapter for memory that lives outside the local store.

The core library is offline and dependency-free by design, so it ships no
Notion / Feishu / Confluence client. What it ships instead is the awkward
half: field mapping, timeouts, error containment and summarisation. You
supply a callable that returns rows; this turns those rows into ranked,
bounded ``RetrievedItem`` objects like any other source.

    def fetch(query):
        return bitable.search(query.text, page_size=50)   # your client

    compass.retrieval.retrievers.append(
        CallableRetriever("feishu", fetch, field_map={"content": "notes"})
    )

Design notes:

* The callable receives the whole ``RetrievalQuery`` so it can push filters
  down to the backend (most APIs do server-side search far better than we
  could client-side).
* It may return dicts or objects with attributes; both are read the same way.
* Rows missing a score get ``default_score``. Remote sources rarely expose an
  ACT-R activation, and ranking them at zero would make them invisible next
  to local memories.
* Anything the callable raises is left to propagate to the orchestrator,
  which records it per-source and carries on. Failing loudly *here* and
  softly *there* is deliberate: it keeps the isolation logic in one place.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .local import count_keyword_hits
from .models import RetrievalQuery, RetrievedItem
from .summarize import DEFAULT_SUMMARY_CHARS, summarize

#: Field names read off each row, and the ``RetrievedItem`` attribute each
#: one feeds. Override individually via ``field_map``.
DEFAULT_FIELD_MAP = {
    "memory_id": "memory_id",
    "content": "content",
    "score": "score",
    "memory_type": "memory_type",
    "keywords": "keywords",
    "age_days": "age_days",
}

#: Remote rows with no score of their own sort just below a mid-importance
#: local memory rather than at the bottom.
DEFAULT_REMOTE_SCORE = 0.5


def _read(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


class CallableRetriever:
    """Wraps any ``(RetrievalQuery) -> rows`` callable as a ``Retriever``."""

    def __init__(
        self,
        name: str,
        fetch: Callable[[RetrievalQuery], Iterable[Any]],
        *,
        field_map: dict[str, str] | None = None,
        default_score: float = DEFAULT_REMOTE_SCORE,
        summary_chars: int = DEFAULT_SUMMARY_CHARS,
        max_rows: int = 200,
    ):
        if not name:
            raise ValueError("a retriever needs a name for provenance and error reporting")
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        self.name = name
        self._fetch = fetch
        self.field_map = {**DEFAULT_FIELD_MAP, **(field_map or {})}
        self.default_score = default_score
        self.summary_chars = summary_chars
        self.max_rows = max_rows

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedItem]:
        keywords = query.effective_keywords()
        rows = list(self._fetch(query) or [])[: self.max_rows]

        items: list[RetrievedItem] = []
        for index, row in enumerate(rows):
            content = str(_read(row, self.field_map["content"], "") or "")
            if not content:
                continue

            # Score locally against the same keyword rules the local store
            # uses, so cross-source ranking stays comparable.
            hits = count_keyword_hits(
                {
                    "content": content,
                    "keywords": _read(row, self.field_map["keywords"], []) or [],
                    "memory_type": _read(row, self.field_map["memory_type"], "") or "",
                },
                keywords,
            )
            summary, truncated = summarize(
                content, keywords=keywords, max_chars=self.summary_chars
            )
            raw_score = _read(row, self.field_map["score"])
            items.append(
                RetrievedItem(
                    memory_id=str(
                        _read(row, self.field_map["memory_id"]) or f"{self.name}:{index}"
                    ),
                    summary=summary,
                    score=float(raw_score) if raw_score is not None else self.default_score,
                    memory_type=str(
                        _read(row, self.field_map["memory_type"], "project_context")
                        or "project_context"
                    ),
                    source=self.name,
                    keyword_hits=hits,
                    age_days=float(_read(row, self.field_map["age_days"], 0.0) or 0.0),
                    formula_version=str(
                        _read(row, "formula_version", "external") or "external"
                    ),
                    truncated=truncated,
                )
            )
        return items
