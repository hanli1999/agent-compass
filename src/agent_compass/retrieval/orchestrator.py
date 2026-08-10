"""Fan out to retrievers, rank, cut to Top-K, and enforce a token budget.

This module is the answer to the problem that motivated the whole layer: an
agent configured with a rich memory store will blow past its model's context
window long before it runs out of relevant memories. Three mechanisms, in
order of application:

1. **Relevance boost** — a keyword match multiplies a memory's activation
   score rather than adding to it. Additive boosts cannot lift a freshly
   written, highly relevant note above a long-established ``identity`` memory
   whose base activation permanently dominates. Multiplicative ones can.

2. **Top-K** — a hard cap on the number of items. Default 7, which is about
   where an agent stops actually reading what it was given.

3. **Token budget** — a hard cap on the *size* of what comes back, applied
   after Top-K. K short memories and K long ones are very different bills.

Everything dropped by (2) or (3) is counted and reported. Silent truncation
that reads as full coverage is the one failure mode this module must not have.
"""
from __future__ import annotations

from .models import RetrievalQuery, RetrievalResult, RetrievedItem, estimate_tokens

#: Each distinct keyword hit multiplies the score by this much.
#: 0.5 means one hit is worth a 1.5x boost and two hits 2.0x — enough for a
#: relevant ``task_lesson`` to overtake an unrelated ``identity`` memory,
#: without letting keyword spam beat activation entirely.
KEYWORD_BOOST_PER_HIT = 0.5

DEFAULT_TOP_K = 7


def relevance_boost(score: float, keyword_hits: int) -> float:
    """``score * (1 + hits * 0.5)`` — see module docstring for why multiplicative."""
    if keyword_hits < 0:
        raise ValueError("keyword_hits must be non-negative")
    return score * (1.0 + keyword_hits * KEYWORD_BOOST_PER_HIT)


class RetrievalOrchestrator:
    """Combines any number of ``Retriever`` implementations into one ranked view."""

    def __init__(self, retrievers, *, default_limit: int = DEFAULT_TOP_K):
        if default_limit < 1:
            raise ValueError("default_limit must be at least 1")
        self.retrievers = list(retrievers)
        self.default_limit = default_limit

    def retrieve(self, query: RetrievalQuery | str, **overrides) -> RetrievalResult:
        """Run ``query`` against every retriever and return a bounded digest.

        A string is accepted as shorthand: its whitespace-split tokens become
        the keywords, which is what a caller passing raw user input wants.
        """
        if isinstance(query, str):
            query = RetrievalQuery(text=query, limit=self.default_limit)
        if overrides:
            query = RetrievalQuery(**{**query.__dict__, **overrides})

        gathered: list[RetrievedItem] = []
        errors: dict[str, str] = {}
        sources: list[str] = []

        for retriever in self.retrievers:
            name = getattr(retriever, "name", retriever.__class__.__name__)
            sources.append(name)
            try:
                gathered.extend(retriever.retrieve(query) or [])
            except Exception as exc:  # noqa: BLE001 - one bad source must not
                # take down the others; recall degrades, it does not fail.
                errors[name] = f"{type(exc).__name__}: {exc}"

        deduped = self._dedupe(gathered)
        ranked = sorted(
            deduped,
            key=lambda i: (-relevance_boost(i.score, i.keyword_hits), i.age_days, i.memory_id),
        )

        considered = len(ranked)
        limit = query.limit or self.default_limit
        kept = ranked[:limit]
        dropped_for_limit = max(0, considered - len(kept))

        kept, dropped_for_budget, total_tokens = self._apply_budget(kept, query.token_budget)

        return RetrievalResult(
            items=kept,
            considered=considered,
            dropped_for_budget=dropped_for_budget,
            dropped_for_limit=dropped_for_limit,
            estimated_tokens=total_tokens,
            sources=sources,
            errors=errors,
        )

    @staticmethod
    def _dedupe(items: list[RetrievedItem]) -> list[RetrievedItem]:
        """Keep the best-scoring copy of each memory_id.

        The same memory can legitimately arrive from two sources (a local
        store and a synced remote one). The caller wants it once.
        """
        best: dict[str, RetrievedItem] = {}
        for item in items:
            key = item.memory_id or f"{item.source}:{item.summary[:32]}"
            current = best.get(key)
            if current is None or relevance_boost(item.score, item.keyword_hits) > relevance_boost(
                current.score, current.keyword_hits
            ):
                best[key] = item
        return list(best.values())

    @staticmethod
    def _apply_budget(
        items: list[RetrievedItem], token_budget: int | None
    ) -> tuple[list[RetrievedItem], int, int]:
        """Pack items in rank order until the budget is exhausted.

        This stops at the first item that does not fit rather than skipping
        ahead to a smaller one. Preserving rank order matters more than
        squeezing the budget dry: an agent reads top-down and expects the
        list to be sorted by relevance, not by what happened to fit.
        """
        total = 0
        if token_budget is None:
            for item in items:
                total += item.estimated_tokens
            return items, 0, total

        kept: list[RetrievedItem] = []
        for index, item in enumerate(items):
            cost = item.estimated_tokens
            if total + cost > token_budget:
                return kept, len(items) - index, total
            kept.append(item)
            total += cost
        return kept, 0, total


def render_digest(result: RetrievalResult, *, header: str = "Relevant memories") -> str:
    """Format a result as the compact text block you paste into a prompt.

    Ends with an explicit note when anything was withheld, so the model is
    told its view is partial instead of inferring that it is complete.
    """
    if not result.items:
        return f"{header}: none."

    lines = [f"{header} ({len(result.items)}):"]
    for index, item in enumerate(result.items, 1):
        marker = "…" if item.truncated else ""
        lines.append(f"{index}. [{item.memory_type}] {item.summary}{marker}")
        lines.append(f"   id={item.memory_id} score={item.score:.3f} hits={item.keyword_hits}")

    withheld = result.dropped_for_limit + result.dropped_for_budget
    if withheld:
        lines.append(
            f"({withheld} further match(es) withheld; "
            f"~{result.estimated_tokens} tokens shown.)"
        )
    if result.errors:
        lines.append(f"(sources unavailable: {', '.join(sorted(result.errors))})")
    return "\n".join(lines)
