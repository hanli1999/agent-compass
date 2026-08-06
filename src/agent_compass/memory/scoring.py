"""Versioned ACT-R-style memory scoring with one public formula."""
from __future__ import annotations

import math
from dataclasses import dataclass

FORMULA_VERSION = "activation-v1"
RETENTION_VERSION = "retention-v1"

IMPORTANCE_WEIGHTS = {
    "identity": 0.9,
    "decision": 0.8,
    "preference": 0.7,
    "workflow_pattern": 0.65,
    "task_lesson": 0.6,
    "project_context": 0.55,
    "temporary_note": 0.2,
}


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    base: float
    context: float
    importance: float
    formula_version: str = FORMULA_VERSION


def retention(days_elapsed: float, stability_days: float) -> float:
    if days_elapsed < 0 or stability_days <= 0:
        raise ValueError("days_elapsed must be non-negative and stability_days positive")
    return math.exp(-days_elapsed / stability_days)


def stability_days(access_count: int, memory_type: str) -> float:
    if access_count < 0:
        raise ValueError("access_count must be non-negative")
    base = {"identity": 60, "decision": 45, "preference": 35, "workflow_pattern": 30, "task_lesson": 21, "project_context": 21, "temporary_note": 7}.get(memory_type, 14)
    return base * math.log2(access_count + 2)


def score_memory(*, access_count: int, days_elapsed: float, keyword_hits: int = 0, memory_type: str = "task_lesson", importance: float | None = None) -> ScoreBreakdown:
    if access_count < 0 or keyword_hits < 0:
        raise ValueError("counts must be non-negative")
    base = math.log1p(access_count) * retention(days_elapsed, stability_days(access_count, memory_type))
    context = min(keyword_hits * 0.15, 0.75)
    imp = IMPORTANCE_WEIGHTS.get(memory_type, 0.3) if importance is None else max(0.0, min(1.0, importance))
    return ScoreBreakdown(round(base + context + imp, 6), round(base, 6), round(context, 6), round(imp, 6))
