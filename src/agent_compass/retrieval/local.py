"""Retriever backed by the local SQLite memory store."""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import MemoryStatus
from .models import RetrievalQuery, RetrievedItem
from .summarize import DEFAULT_SUMMARY_CHARS, summarize

#: Statuses that are invisible to retrieval unless explicitly requested.
_HIDDEN_STATUSES = {MemoryStatus.ARCHIVED.value, MemoryStatus.DELETED.value}


def _days_since(iso_ts: str | None, now: datetime) -> float:
    if not iso_ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(iso_ts)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def count_keyword_hits(memory: dict, keywords: list[str]) -> int:
    """How many distinct query keywords this memory matches.

    A keyword counts once no matter how many fields it appears in — otherwise
    a single word repeated in content, keywords and type would triple-count
    and swamp a memory that genuinely matches three different terms.
    """
    if not keywords:
        return 0
    haystack = " ".join(
        [
            str(memory.get("content", "")),
            " ".join(str(k) for k in (memory.get("keywords") or [])),
            str(memory.get("memory_type", "")),
        ]
    ).lower()
    return sum(1 for k in keywords if k in haystack)


class LocalMemoryRetriever:
    """Reads the local store, filters, and summarises. Implements ``Retriever``.

    Ranking is *not* done here — the orchestrator owns that so results from
    multiple sources stay comparable.
    """

    name = "local"

    def __init__(self, store, *, summary_chars: int = DEFAULT_SUMMARY_CHARS):
        self.store = store
        self.summary_chars = summary_chars

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedItem]:
        keywords = query.effective_keywords()
        now = datetime.now(timezone.utc)

        # Pull wide, then narrow locally: the store's own ordering is by
        # updated_at, which is not the ranking we want.
        candidates = self.store.list_memories(limit=10_000)

        items: list[RetrievedItem] = []
        for memory in candidates:
            status = memory.get("status")
            if not query.include_archived and status in _HIDDEN_STATUSES:
                continue
            if query.memory_type and memory.get("memory_type") != query.memory_type:
                continue

            age = _days_since(memory.get("created_at"), now)
            if query.since_days is not None and age > query.since_days:
                continue

            hits = count_keyword_hits(memory, keywords)
            # With an explicit query, a zero-hit memory is noise.
            if keywords and hits == 0:
                continue

            summary, truncated = summarize(
                memory.get("content", ""),
                keywords=keywords,
                max_chars=self.summary_chars,
            )
            items.append(
                RetrievedItem(
                    memory_id=memory.get("memory_id", ""),
                    summary=summary,
                    score=float(memory.get("score") or 0.0),
                    memory_type=memory.get("memory_type", "task_lesson"),
                    source=self.name,
                    keyword_hits=hits,
                    age_days=round(age, 3),
                    formula_version=memory.get("formula_version") or "activation-v1",
                    truncated=truncated,
                )
            )
        return items
