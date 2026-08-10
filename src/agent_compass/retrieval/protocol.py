"""The Retriever protocol every memory backend implements."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import RetrievalQuery, RetrievedItem


@runtime_checkable
class Retriever(Protocol):
    """A source of memories the orchestrator can fan out to.

    Implementations are expected to be *cheap and best-effort*. The
    orchestrator applies the final ranking, the Top-K cut and the token
    budget itself, so a retriever should not try to be clever about those:
    return what matches, ordered however is natural for the backend, and let
    the orchestrator arbitrate across sources.

    A retriever that raises is isolated, not fatal — the orchestrator records
    the error under its ``name`` and continues with the remaining sources. A
    flaky network-backed store must never take down local recall.
    """

    @property
    def name(self) -> str:
        """Stable identifier, used for provenance and error reporting."""
        ...

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedItem]:
        """Return candidate items for ``query``. Summaries only, never bodies."""
        ...
