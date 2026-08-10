"""Property-based tests using Hypothesis.

These tests pin invariants that must hold for arbitrary inputs. They run
slower than the unit tests but catch edge cases the example-based tests
cannot, and they double as living documentation of the public contracts.
"""
from __future__ import annotations

import string
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from agent_compass.memory.scoring import (
    FORMULA_VERSION,
    retention,
    score_memory,
    stability_days,
)
from agent_compass.models import (
    Task,
    TaskStatus,
)
from agent_compass.privacy.boundary import (
    PrivacyBoundary,
    PrivacyConfig,
    PrivacyLevel,
)
from agent_compass.tasks.state_machine import TaskStateMachine


# ---------- scoring ----------

# Memory types that the scoring formula understands.
MEMORY_TYPES = st.sampled_from(
    [
        "identity",
        "decision",
        "preference",
        "workflow_pattern",
        "task_lesson",
        "project_context",
        "temporary_note",
        "unknown_type_for_fuzz",
    ]
)

non_negative_int = st.integers(min_value=0, max_value=10_000)
small_float = st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)


@given(
    access_count=non_negative_int,
    days=small_float,
    keyword_hits=st.integers(min_value=0, max_value=200),
    memory_type=MEMORY_TYPES,
    importance=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_score_memory_is_non_negative(access_count, days, keyword_hits, memory_type, importance):
    result = score_memory(
        access_count=access_count,
        days_elapsed=days,
        keyword_hits=keyword_hits,
        memory_type=memory_type,
        importance=importance,
    )
    assert result.score >= 0.0
    assert result.context <= 0.75  # bounded by scoring.py
    assert result.importance == max(0.0, min(1.0, importance))
    assert result.formula_version == FORMULA_VERSION


@given(days=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False))
def test_retention_is_in_unit_interval(days):
    value = retention(days, stability_days=30.0)
    assert 0.0 <= value <= 1.0


@given(days=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False))
def test_retention_is_monotonically_decreasing(days):
    if days >= 9_999.0:
        # avoid overflow at the upper edge
        return
    a = retention(days, 30.0)
    b = retention(days + 1.0, 30.0)
    assert b <= a


# ---------- privacy boundary ----------


SECRET_TRIGGERS = [
    "-----BEGIN RSA PRIVATE KEY-----",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
    "api_key=abcdefghijklmnop",
    'password=hunter2hunter2',
]


@given(
    text=st.text(
        alphabet=string.ascii_letters + string.digits + " \n\t-_:./@",
        min_size=0,
        max_size=200,
    )
)
@settings(max_examples=200)
def test_privacy_inspect_never_crashes(text):
    """The detector must handle any text without raising."""
    boundary = PrivacyBoundary()
    inspection = boundary.inspect(text)
    assert inspection.level in PrivacyLevel
    assert isinstance(inspection.matches, tuple)
    assert isinstance(inspection.blocked, bool)


@given(secret=st.sampled_from(SECRET_TRIGGERS))
def test_secret_triggers_are_blocked(secret):
    boundary = PrivacyBoundary()
    inspection = boundary.inspect(secret)
    assert inspection.level == PrivacyLevel.SECRET
    assert inspection.blocked


@given(
    text=st.text(
        alphabet=string.ascii_letters + string.digits + " ",
        min_size=1,
        max_size=200,
    )
)
@settings(max_examples=200)
def test_redact_never_grows_unboundedly(text):
    """Redaction must not blow up short text into something much longer."""
    boundary = PrivacyBoundary()
    out = boundary.redact(text)
    assert len(out) <= len(text) + 200  # plenty of slack for tag overhead


def test_custom_privacy_config_overlays():
    config = PrivacyConfig(
        extra_sensitive=(("custom_id", __import__("re").compile(r"\bEMP-\d{6}\b")),),
    )
    boundary = PrivacyBoundary(config)
    result = boundary.inspect("see EMP-123456 in the ticket")
    assert "custom_id" in result.matches
    assert result.level == PrivacyLevel.SENSITIVE


# ---------- state machine ----------


@given(
    start=st.sampled_from(list(TaskStatus)),
)
@settings(max_examples=200)
def test_task_state_machine_does_not_corrupt(start):
    """Reaching any state from any start must not raise, but invalid
    transitions must raise. The invariant: a valid sequence of allowed
    transitions ends with a status that exists."""
    machine = TaskStateMachine()
    task = Task(goal="fuzz")
    # start the task in the supplied state by walking a valid path
    path = _valid_path_to(start)
    for step in path:
        machine.transition(task, step)
    assert task.status == start


def _valid_path_to(target: TaskStatus) -> list[TaskStatus]:
    # A simple path that any state can reach.
    base = [TaskStatus.PLANNED, TaskStatus.RUNNING]
    if target in (TaskStatus.CREATED,):
        return []
    if target in (TaskStatus.PLANNED,):
        return [TaskStatus.PLANNED]
    if target in (TaskStatus.RUNNING,):
        return base
    # Everything else: walk to RUNNING then to target if allowed.
    if target in (TaskStatus.WAITING_FOR_USER,):
        return base + [target]
    if target in (TaskStatus.WAITING_FOR_APPROVAL,):
        return base + [target]
    if target in (TaskStatus.BLOCKED,):
        return base + [TaskStatus.BLOCKED]
    if target in (TaskStatus.COMPLETED,):
        return base + [TaskStatus.COMPLETED]
    if target in (TaskStatus.FAILED,):
        return base + [TaskStatus.FAILED]
    if target in (TaskStatus.CANCELLED,):
        return base + [target]
    if target in (TaskStatus.ARCHIVED,):
        return base + [target]
    return base


@given(
    start=st.sampled_from(list(TaskStatus)),
    target=st.sampledfrom := st.sampled_from(list(TaskStatus)),
)
@settings(max_examples=200)
def test_invalid_transitions_raise(start, target):
    machine = TaskStateMachine()
    task = Task(goal="fuzz")
    # Walk to start state.
    for step in _valid_path_to(start):
        machine.transition(task, step)
    # If the target is reachable from start via the allow-list, expect success.
    allowed = {
        TaskStatus.CREATED: {TaskStatus.PLANNED, TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.PLANNED: {TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.RUNNING: {TaskStatus.WAITING_FOR_USER, TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.WAITING_FOR_USER: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.WAITING_FOR_APPROVAL: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.BLOCKED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.COMPLETED: {TaskStatus.ARCHIVED},
        TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.ARCHIVED},
        TaskStatus.CANCELLED: {TaskStatus.ARCHIVED},
        TaskStatus.ARCHIVED: set(),
    }
    if target in allowed[start]:
        machine.transition(task, target)  # should not raise
        assert task.status == target
    else:
        with pytest.raises(ValueError):
            machine.transition(task, target)
