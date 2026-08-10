"""Versioned ACT-R-style memory scoring.

Two formulas coexist. Which one runs is decided by the record's
``formula_version`` field, so upgrading the library never silently rescores
existing rows.

activation-v1 (default, unchanged since 0.2.0)
    score = base + context + importance

activation-v2 (opt-in, new in 0.4.0)
    score = base + (context + importance + emotion + instinct) / 4

    The four auxiliary dimensions are each normalised to [0, 1] and averaged,
    so no single dimension can dominate ``base``. ``base`` itself is left in
    its native ACT-R magnitude (roughly 0-5): it means "probability this
    memory is activated", and squashing it into [0, 1] would throw away the
    frequency signal.

    v2 adds two biologically-motivated dimensions:

    emotion  - affect tag attached to the memory. Emotionally charged events
               are recalled more readily (amygdala modulation of consolidation).
    instinct - a five-class minimal drive set, chosen to be the smallest set
               that already exists in single-celled organisms:

               survival  threat / loss sensitivity
               resource  energy and resource acquisition
               transmit  shareable / replicable information
               kin       social distance, relationship chains
               novelty   novelty and conflict detection

    v2 also switches the default retention curve to ``retention_dual``: steep
    decay for the first week, flat afterwards. A single exponential forces all
    memories onto the same curve and cannot express "this one was already
    reconsolidated, stop decaying it".
"""
from __future__ import annotations

import math
from dataclasses import dataclass

FORMULA_VERSION = "activation-v1"
FORMULA_VERSION_V2 = "activation-v2"
RETENTION_VERSION = "retention-v1"
RETENTION_VERSION_DUAL = "retention-dual-v1"

SUPPORTED_FORMULA_VERSIONS = (FORMULA_VERSION, FORMULA_VERSION_V2)

IMPORTANCE_WEIGHTS = {
    "identity": 0.9,
    "decision": 0.8,
    "event": 0.75,
    "preference": 0.7,
    "workflow_pattern": 0.65,
    "task_lesson": 0.6,
    "project_context": 0.55,
    "temporary_note": 0.2,
}

#: Affect tags. Values are relative recall multipliers, capped at 1.0 by the
#: v2 formula; anything above 1.0 simply saturates.
EMOTION_WEIGHTS = {
    "excited": 1.2,
    "happy": 1.3,
    "longing": 1.1,
    "neutral": 1.0,
    "anxious": 0.9,
    "sad": 0.8,
}

#: Five-class minimal instinct set (see module docstring).
INSTINCT_WEIGHTS = {
    "survival": 0.85,
    "kin": 0.75,
    "resource": 0.70,
    "novelty": 0.65,
    "transmit": 0.60,
}

TYPE_STABILITY = {
    "identity": 60,
    "decision": 45,
    "event": 365,
    "preference": 35,
    "workflow_pattern": 30,
    "task_lesson": 21,
    "project_context": 21,
    "temporary_note": 7,
}

#: Boundary between short-term (steep) and long-term (flat) decay, in days.
DUAL_RETENTION_SWITCH_DAYS = 7.0

#: Raw ``keyword_hits * 0.15`` is clipped here before being normalised to
#: [0, 1]. 0.5 was chosen over 0.75 after an A/B run over a real 40-document
#: memory store: 5 queries x top-7 produced zero recall difference, so the
#: tighter cap wins on the tie-break.
DEFAULT_CONTEXT_CAP = 0.5

CONTEXT_PER_HIT = 0.15


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    base: float
    context: float
    importance: float
    emotion: float = 0.0
    instinct: float = 0.0
    formula_version: str = FORMULA_VERSION


def retention(days_elapsed: float, stability_days: float) -> float:
    """Single exponential (Ebbinghaus). Used by activation-v1."""
    if days_elapsed < 0 or stability_days <= 0:
        raise ValueError("days_elapsed must be non-negative and stability_days positive")
    return math.exp(-days_elapsed / stability_days)


def retention_dual(
    days_elapsed: float,
    stability_days: float,
    switch_point: float = DUAL_RETENTION_SWITCH_DAYS,
) -> float:
    """Two-segment retention: steep near, flat far. Used by activation-v2.

    Below ``switch_point`` the effective stability is halved (fast forgetting).
    Above it the effective stability is doubled (post-reconsolidation plateau).
    The two segments are joined at ``switch_point`` so the curve is continuous.
    """
    if days_elapsed < 0 or stability_days <= 0:
        raise ValueError("days_elapsed must be non-negative and stability_days positive")
    if switch_point <= 0:
        raise ValueError("switch_point must be positive")
    if days_elapsed <= switch_point:
        return math.exp(-days_elapsed / (stability_days * 0.5))
    at_switch = math.exp(-switch_point / (stability_days * 0.5))
    return at_switch * math.exp(-(days_elapsed - switch_point) / (stability_days * 2.0))


def stability_days(access_count: int, memory_type: str) -> float:
    """Stability grows logarithmically with access count (Hebbian reconsolidation)."""
    if access_count < 0:
        raise ValueError("access_count must be non-negative")
    base = TYPE_STABILITY.get(memory_type, 14)
    return base * math.log2(access_count + 2)


def _resolve_importance(memory_type: str, importance: float | None) -> float:
    if importance is None:
        return IMPORTANCE_WEIGHTS.get(memory_type, 0.3)
    return max(0.0, min(1.0, importance))


def score_memory(
    *,
    access_count: int,
    days_elapsed: float,
    keyword_hits: int = 0,
    memory_type: str = "task_lesson",
    importance: float | None = None,
) -> ScoreBreakdown:
    """activation-v1: ``base + context + importance``."""
    if access_count < 0 or keyword_hits < 0:
        raise ValueError("counts must be non-negative")
    base = math.log1p(access_count) * retention(days_elapsed, stability_days(access_count, memory_type))
    context = min(keyword_hits * CONTEXT_PER_HIT, 0.75)
    imp = _resolve_importance(memory_type, importance)
    return ScoreBreakdown(
        round(base + context + imp, 6),
        round(base, 6),
        round(context, 6),
        round(imp, 6),
        formula_version=FORMULA_VERSION,
    )


def score_memory_v2(
    *,
    access_count: int,
    days_elapsed: float,
    keyword_hits: int = 0,
    memory_type: str = "task_lesson",
    importance: float | None = None,
    emotion_tag: str | None = None,
    instinct_tag: str | None = None,
    use_dual_retention: bool = True,
    context_cap: float = DEFAULT_CONTEXT_CAP,
) -> ScoreBreakdown:
    """activation-v2: ``base + mean(context, importance, emotion, instinct)``.

    Unknown ``emotion_tag`` / ``instinct_tag`` values contribute 0 rather than
    raising, so callers can pass through free-form tags from upstream systems.
    """
    if access_count < 0 or keyword_hits < 0:
        raise ValueError("counts must be non-negative")
    if context_cap <= 0:
        raise ValueError("context_cap must be positive")

    stability = stability_days(access_count, memory_type)
    curve = retention_dual if use_dual_retention else retention
    base = math.log1p(access_count) * curve(days_elapsed, stability)

    context = min(keyword_hits * CONTEXT_PER_HIT, context_cap) / context_cap
    imp = _resolve_importance(memory_type, importance)
    emotion = min(EMOTION_WEIGHTS.get(emotion_tag or "", 0.0), 1.0)
    instinct = min(INSTINCT_WEIGHTS.get(instinct_tag or "", 0.0), 1.0)

    auxiliary = (context + imp + emotion + instinct) / 4.0
    return ScoreBreakdown(
        round(base + auxiliary, 6),
        round(base, 6),
        round(context, 6),
        round(imp, 6),
        round(emotion, 6),
        round(instinct, 6),
        formula_version=FORMULA_VERSION_V2,
    )


def route_score(
    *,
    access_count: int,
    days_elapsed: float,
    keyword_hits: int = 0,
    memory_type: str = "task_lesson",
    importance: float | None = None,
    emotion_tag: str | None = None,
    instinct_tag: str | None = None,
    formula_version: str = FORMULA_VERSION,
) -> ScoreBreakdown:
    """Dispatch to the formula a record was written with.

    Records carrying no (or an unknown) ``formula_version`` fall back to v1,
    which is what every pre-0.4.0 row is.
    """
    if formula_version == FORMULA_VERSION_V2:
        return score_memory_v2(
            access_count=access_count,
            days_elapsed=days_elapsed,
            keyword_hits=keyword_hits,
            memory_type=memory_type,
            importance=importance,
            emotion_tag=emotion_tag,
            instinct_tag=instinct_tag,
        )
    return score_memory(
        access_count=access_count,
        days_elapsed=days_elapsed,
        keyword_hits=keyword_hits,
        memory_type=memory_type,
        importance=importance,
    )
