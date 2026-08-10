"""Value objects for the retrieval layer."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

#: Rough characters-per-token ratio. Deliberately conservative: CJK text is
#: closer to 1 char/token while English prose is closer to 4, and over-
#: estimating the budget is the failure mode that actually hurts (a blown
#: context window), so we assume the expensive case.
CHARS_PER_TOKEN = 2.0


def estimate_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate.

    We are not trying to match any particular tokenizer. We only need a
    monotonic, tokenizer-agnostic proxy so the orchestrator can stop packing
    results before it overruns a caller's context budget.
    """
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN + 0.5))


@dataclass(frozen=True)
class RetrievalQuery:
    """What the caller is looking for.

    ``keywords`` drives the relevance boost. If it is empty the orchestrator
    falls back to pure activation ranking, which answers "what is most
    salient right now" rather than "what matches this query".
    """

    text: str = ""
    keywords: list[str] = field(default_factory=list)
    memory_type: str | None = None
    limit: int = 7
    token_budget: int | None = None
    since_days: float | None = None
    include_archived: bool = False

    def __post_init__(self):
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.token_budget is not None and self.token_budget < 1:
            raise ValueError("token_budget must be positive when set")
        if self.since_days is not None and self.since_days < 0:
            raise ValueError("since_days must be non-negative")

    def effective_keywords(self) -> list[str]:
        """Keywords plus whitespace-split query text, lowercased and deduped."""
        seen: set[str] = set()
        out: list[str] = []
        for token in [*self.keywords, *self.text.split()]:
            low = str(token).strip().lower()
            if low and low not in seen:
                seen.add(low)
                out.append(low)
        return out


@dataclass(frozen=True)
class RetrievedItem:
    """A single result. Carries a *summary*, never the full content.

    ``memory_id`` is the handle a caller uses to fetch the full text on
    demand. That indirection is the whole point of this layer: the agent sees
    a bounded digest and pulls the body only for the one or two entries it
    actually needs.
    """

    memory_id: str
    summary: str
    score: float
    memory_type: str = "task_lesson"
    source: str = "local"
    keyword_hits: int = 0
    age_days: float = 0.0
    formula_version: str = "activation-v1"
    truncated: bool = False

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.summary)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["estimated_tokens"] = self.estimated_tokens
        return value


@dataclass(frozen=True)
class RetrievalResult:
    """The orchestrator's answer, including what it had to leave out.

    ``dropped_for_budget`` and ``dropped_for_limit`` exist so a silent
    truncation can never masquerade as full coverage. Callers that care can
    surface "3 more matches were not shown" to the user.
    """

    items: list[RetrievedItem] = field(default_factory=list)
    considered: int = 0
    dropped_for_budget: int = 0
    dropped_for_limit: int = 0
    estimated_tokens: int = 0
    sources: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    @property
    def truncated(self) -> bool:
        return bool(self.dropped_for_budget or self.dropped_for_limit)

    def memory_ids(self) -> list[str]:
        return [i.memory_id for i in self.items]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "considered": self.considered,
            "dropped_for_budget": self.dropped_for_budget,
            "dropped_for_limit": self.dropped_for_limit,
            "estimated_tokens": self.estimated_tokens,
            "truncated": self.truncated,
            "sources": list(self.sources),
            "errors": dict(self.errors),
        }
