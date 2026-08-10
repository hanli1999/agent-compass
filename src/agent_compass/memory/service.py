"""Memory service: full lifecycle from candidate to archived/deleted."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import MemoryCandidate, MemoryStatus, utc_now
from ..privacy.boundary import PrivacyBoundary
from .scoring import (
    FORMULA_VERSION,
    FORMULA_VERSION_V2,
    SUPPORTED_FORMULA_VERSIONS,
    route_score,
)

PRUNE_BELOW_SCORE = 0.15
STALE_BELOW_SCORE = 0.3
DEFAULT_CONSOLIDATE_THRESHOLD = 0.6
DEFAULT_MERGE_THRESHOLD = 0.5


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


_STOPWORDS = frozenset(
    """a an the of to for on in at by with from and or but if is are was were be been being
    do does did has have had this that these those it its their there here not no
    你 我 他 她 它 的 了 在 是 有 和 与 及 或 但 如果""".split()
)


def _tokenize(text: str) -> set[str]:
    """Lightweight tokenizer: lowercase, split on non-word chars, drop stopwords.

    Works for both English and Chinese (treats each CJK character as a token).
    Not as smart as MeCab or jieba, but it stays dependency-free and is enough
    to catch duplicate ``task_lesson`` content.
    """
    if not text:
        return set()
    lowered = text.lower()
    tokens: list[str] = []
    buf: list[str] = []
    for ch in lowered:
        if ch.isalnum() or ch in {"_", "-"}:
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
            if "一" <= ch <= "鿿":
                tokens.append(ch)
    if buf:
        tokens.append("".join(buf))
    return {t for t in tokens if t and t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    intersection = a & b
    union = a | b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _rescore(memory: dict, days_elapsed: float):
    """Rescore a stored row using the formula it was written with."""
    return route_score(
        access_count=memory.get("access_count", 0),
        days_elapsed=days_elapsed,
        keyword_hits=len(memory.get("keywords", []) or []),
        memory_type=memory.get("memory_type", "task_lesson"),
        importance=memory.get("importance"),
        emotion_tag=memory.get("emotion_tag"),
        instinct_tag=memory.get("instinct_tag"),
        formula_version=memory.get("formula_version") or FORMULA_VERSION,
    )


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
        emotion_tag: str | None = None,
        instinct_tag: str | None = None,
        formula_version: str | None = None,
    ) -> MemoryCandidate:
        inspection = self.boundary.inspect(content)
        if inspection.blocked:
            raise ValueError(
                f"secret content cannot become a memory: {', '.join(inspection.matches)}"
            )
        chosen_privacy = privacy or self._default_privacy(inspection)
        # Tagging a memory with affect or drive only means anything under v2,
        # so opt the record in automatically rather than silently ignoring it.
        if formula_version is None:
            formula_version = (
                FORMULA_VERSION_V2 if (emotion_tag or instinct_tag) else FORMULA_VERSION
            )
        if formula_version not in SUPPORTED_FORMULA_VERSIONS:
            raise ValueError(f"unsupported formula_version: {formula_version}")
        candidate = MemoryCandidate(
            content=content,
            memory_type=memory_type,
            privacy=chosen_privacy,
            keywords=list(keywords or []),
            importance=importance if importance is not None else 0.5,
            novelty=novelty,
            source=source,
            related_task_id=related_task_id,
            emotion_tag=emotion_tag,
            instinct_tag=instinct_tag,
            formula_version=formula_version,
        )
        candidate.score = route_score(
            access_count=0,
            days_elapsed=0.0,
            keyword_hits=len(candidate.keywords),
            memory_type=candidate.memory_type,
            importance=candidate.importance,
            emotion_tag=emotion_tag,
            instinct_tag=instinct_tag,
            formula_version=formula_version,
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

    def get(self, memory_id: str) -> dict[str, Any] | None:
        """Fetch one memory in full.

        This is the other half of the retrieval layer's contract: recall
        returns bounded summaries, and the caller expands the one or two
        entries it actually needs through here.
        """
        return self.store.get_memory(memory_id)

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
        result = _rescore(memory, days)
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
            result = _rescore(memory, days)
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

    def consolidate(
        self,
        *,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        status_filter: list[MemoryStatus | str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Find similar memories and merge them.

        Two memories are similar when the Jaccard similarity of their tokenized
        content (augmented with their keywords) is at or above
        ``merge_threshold``. For each cluster the highest-score memory becomes
        the survivor; siblings are demoted to ``archived`` and recorded in the
        survivor's ``merged_from`` list (stored under the ``metadata.merged_from``
        JSON field; the SQLite row keeps the original columns and we add the
        new list inline).
        """
        if not 0.0 < merge_threshold <= 1.0:
            raise ValueError("merge_threshold must be in (0, 1]")

        status_values: set[str] | None = None
        if status_filter:
            status_values = {
                s.value if isinstance(s, MemoryStatus) else str(s) for s in status_filter
            }
        candidates = self.store.list_memories(limit=10_000)
        if status_values is not None:
            candidates = [m for m in candidates if m.get("status") in status_values]

        # Pre-compute token sets once.
        decorated: list[tuple[dict, set[str]]] = []
        for memory in candidates:
            tokens = _tokenize(memory.get("content", ""))
            for kw in memory.get("keywords", []) or []:
                tokens.add(str(kw).lower())
            decorated.append((memory, tokens))

        clusters: list[list[int]] = []
        for i in range(len(decorated)):
            for cluster in clusters:
                representative = decorated[cluster[0]][1]
                if _jaccard(decorated[i][1], representative) >= merge_threshold:
                    cluster.append(i)
                    break
            else:
                clusters.append([i])

        merged_clusters = 0
        merged_memories = 0
        survivors: list[dict] = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            members = [decorated[i][0] for i in cluster]
            members.sort(
                key=lambda m: (m.get("score") or 0.0, m.get("access_count", 0)),
                reverse=True,
            )
            survivor = dict(members[0])
            siblings = members[1:]
            survivor["access_count"] = int(survivor.get("access_count", 0)) + sum(
                int(s.get("access_count", 0)) for s in siblings
            )
            survivor_keywords = set(survivor.get("keywords", []) or [])
            for sibling in siblings:
                survivor_keywords.update(sibling.get("keywords", []) or [])
            survivor["keywords"] = sorted(survivor_keywords)
            survivor["merged_from"] = [s["memory_id"] for s in siblings]
            now = datetime.now(timezone.utc)
            days = _days_between(survivor.get("created_at"), now)
            result = _rescore(survivor, days)
            survivor["score"] = result.score
            survivor["status"] = MemoryStatus.ACTIVE.value
            survivor["updated_at"] = utc_now()
            if not dry_run:
                self.store.save_memory(survivor)
                for sibling in siblings:
                    sibling["status"] = MemoryStatus.ARCHIVED.value
                    sibling["merged_into"] = survivor["memory_id"]
                    sibling["updated_at"] = utc_now()
                    self.store.save_memory(sibling)
            merged_clusters += 1
            merged_memories += len(siblings)
            survivors.append(survivor)

        return {
            "candidates": len(candidates),
            "clusters": len(clusters),
            "merged_clusters": merged_clusters,
            "merged_memories": merged_memories,
            "survivors": [s["memory_id"] for s in survivors],
            "dry_run": dry_run,
            "merge_threshold": merge_threshold,
        }

    def _default_privacy(self, inspection) -> str:
        if inspection.level.value >= 2:  # SENSITIVE or SECRET
            return "sensitive"
        return "local_only"
