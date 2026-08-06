"""Memory service: full lifecycle from candidate to archived/deleted."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import MemoryCandidate, MemoryStatus, utc_now
from ..privacy.boundary import PrivacyBoundary
from .scoring import score_memory

PRUNE_BELOW_SCORE = 0.15
STALE_BELOW_SCORE = 0.3


def _days_between(iso_ts: str | None, now: datetime) -> float:
    if not iso_ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(iso_ts)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


class MemoryService:
    def __init__(self, boundary: PrivacyBoundary, store):
        self.boundary = boundary
        self.store = store

    def propose(
        self,
        content: str,
        *,
        memory_type: str = "task_lesson",
        privacy: str | None = None,
        keywords: list[str] | None = None,
        importance: float | None = None,
        novelty: float = 0.5,
        source: str = "session",
        related_task_id: str | None = None,
    ) -> MemoryCandidate:
        inspection = self.boundary.inspect(content)
        if inspection.blocked:
            raise ValueError(
                f"secret content cannot become a memory: {', '.join(inspection.matches)}"
            )
        chosen_privacy = privacy or self._default_privacy(inspection)
        candidate = MemoryCandidate(
            content=content,
            memory_type=memory_type,
            privacy=chosen_privacy,
            keywords=list(keywords or []),
            importance=importance if importance is not None else 0.5,
            novelty=novelty,
            source=source,
            related_task_id=related_task_id,
        )
        candidate.score = score_memory(
            access_count=0,
            days_elapsed=0.0,
            keyword_hits=len(candidate.keywords),
            memory_type=candidate.memory_type,
            importance=candidate.importance,
        ).score
        candidate.status = MemoryStatus.CANDIDATE
        self.store.save_memory(candidate.to_dict())
        return candidate

    def accept(self, memory_id: str) -> MemoryCandidate:
        return self._transition(memory_id, MemoryStatus.ACCEPTED, allowed_from={MemoryStatus.CANDIDATE, MemoryStatus.STALE})

    def activate(self, memory_id: str) -> MemoryCandidate:
        return self._transition(memory_id, MemoryStatus.ACTIVE, allowed_from={MemoryStatus.ACCEPTED, MemoryStatus.STALE})

    def archive(self, memory_id: str) -> MemoryCandidate:
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        memory["status"] = MemoryStatus.ARCHIVED.value
        memory["updated_at"] = utc_now()
        self.store.save_memory(memory)
        return self._hydrate(memory)

    def delete(self, memory_id: str) -> bool:
        return self.store.delete_memory(memory_id)

    def list(
        self,
        *,
        status: MemoryStatus | str | None = None,
        privacy: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        status_value = status.value if isinstance(status, MemoryStatus) else status
        return self.store.list_memories(status=status_value, privacy=privacy, limit=limit)

    def search(
        self,
        *,
        query: str | None = None,
        memory_type: str | None = None,
        min_score: float | None = None,
        status: MemoryStatus | str | None = None,
        privacy: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        status_value = status.value if isinstance(status, MemoryStatus) else status
        return self.store.search_memories(
            query=query,
            memory_type=memory_type,
            min_score=min_score,
            status=status_value,
            privacy=privacy,
            limit=limit,
        )

    def touch(self, memory_id: str) -> MemoryCandidate:
        """Record an access (used by retrieval callers) and recompute score."""
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        memory["access_count"] = int(memory.get("access_count", 0)) + 1
        memory["last_accessed"] = utc_now()
        now = datetime.now(timezone.utc)
        days = _days_between(memory.get("created_at"), now)
        result = score_memory(
            access_count=memory["access_count"],
            days_elapsed=days,
            keyword_hits=len(memory.get("keywords", [])),
            memory_type=memory.get("memory_type", "task_lesson"),
            importance=memory.get("importance"),
        )
        memory["score"] = result.score
        memory["updated_at"] = utc_now()
        if memory.get("status") == MemoryStatus.STALE.value and result.score >= STALE_BELOW_SCORE:
            memory["status"] = MemoryStatus.ACTIVE.value
        self.store.save_memory(memory)
        return self._hydrate(memory)

    def prune(
        self,
        *,
        below: float = PRUNE_BELOW_SCORE,
        stale_below: float = STALE_BELOW_SCORE,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Score every memory; demote to STALE or ARCHIVED based on score.

        Returns a summary of how many memories moved through each transition.
        """
        archived = stale = kept = 0
        now = datetime.now(timezone.utc)
        for memory in self.store.list_memories(limit=10_000):
            days = _days_between(memory.get("created_at"), now)
            result = score_memory(
                access_count=memory.get("access_count", 0),
                days_elapsed=days,
                keyword_hits=len(memory.get("keywords", [])),
                memory_type=memory.get("memory_type", "task_lesson"),
                importance=memory.get("importance"),
            )
            memory["score"] = result.score
            current = memory.get("status")
            if result.score < below:
                memory["status"] = MemoryStatus.ARCHIVED.value
                archived += 1
            elif result.score < stale_below and current not in {MemoryStatus.ARCHIVED.value, MemoryStatus.DELETED.value}:
                memory["status"] = MemoryStatus.STALE.value
                stale += 1
            else:
                kept += 1
            if not dry_run:
                memory["updated_at"] = utc_now()
                self.store.save_memory(memory)
        return {"archived": archived, "stale": stale, "kept": kept, "dry_run": int(dry_run)}

    def _transition(
        self,
        memory_id: str,
        target: MemoryStatus,
        *,
        allowed_from: set[MemoryStatus],
    ) -> MemoryCandidate:
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        current = MemoryStatus(memory["status"])
        if current not in allowed_from:
            raise ValueError(f"invalid memory transition: {current.value} -> {target.value}")
        memory["status"] = target.value
        memory["updated_at"] = utc_now()
        self.store.save_memory(memory)
        return self._hydrate(memory)

    def _hydrate(self, memory: dict) -> MemoryCandidate:
        fields = {k: v for k, v in memory.items() if k in MemoryCandidate.__dataclass_fields__}
        fields["status"] = MemoryStatus(fields["status"])
        return MemoryCandidate(**fields)

    def _default_privacy(self, inspection) -> str:
        if inspection.level.value >= 2:  # SENSITIVE or SECRET
            return "sensitive"
        return "local_only"
