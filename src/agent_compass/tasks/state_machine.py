"""Task state machine, checkpoints, and approval semantics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import Task, TaskStatus, utc_now

_ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
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


@dataclass
class Checkpoint:
    task_id: str
    phase: str
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)


class TaskStateMachine:
    def transition(self, task: Task, target: TaskStatus, *, reason: str = "") -> Task:
        if target not in _ALLOWED[task.status]:
            raise ValueError(f"invalid task transition: {task.status.value} -> {target.value}")
        task.status = target
        task.blocked_reason = reason if target in {TaskStatus.BLOCKED, TaskStatus.FAILED} else ""
        task.updated_at = utc_now()
        return task

    def checkpoint(self, task: Task, checkpoint: Checkpoint) -> Task:
        task.current_phase = checkpoint.phase
        task.completed_steps = list(checkpoint.completed_steps)
        task.pending_steps = list(checkpoint.pending_steps)
        task.updated_at = checkpoint.created_at
        task.metadata["checkpoint"] = {
            "phase": checkpoint.phase,
            "completed_steps": checkpoint.completed_steps,
            "pending_steps": checkpoint.pending_steps,
            "notes": checkpoint.notes,
            "artifacts": checkpoint.artifacts,
            "created_at": checkpoint.created_at,
        }
        return task

    def resume(self, task: Task) -> dict[str, Any]:
        checkpoint = task.metadata.get("checkpoint", {})
        if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return {"resume": False, "requires_user": False, "reason": "terminal_state"}
        if task.status == TaskStatus.WAITING_FOR_APPROVAL:
            return {"resume": False, "requires_user": True, "reason": "approval_pending", "checkpoint": checkpoint}
        return {"resume": True, "requires_user": False, "checkpoint": checkpoint, "next_step": (task.pending_steps or [None])[0]}


class _StoreLike(Protocol):
    def has_idempotency_key(self, key: str) -> bool: ...
    def record_idempotency_key(self, key: str, scope: str, task_id: str | None = None) -> None: ...


class IdempotencyRegistry:
    """Track idempotency keys with optional durable storage.

    When a store is provided, the registry survives process restarts so that
    retries after a crash do not double-execute side effects. Without a store
    the registry falls back to in-memory tracking, which is appropriate for
    short-lived unit tests and one-shot scripts.
    """

    def __init__(self, store: _StoreLike | None = None) -> None:
        self._store = store
        self._committed: set[str] = set()

    def can_execute(self, key: str) -> bool:
        if self._store is not None:
            return not self._store.has_idempotency_key(key)
        return key not in self._committed

    def commit(self, key: str, *, scope: str = "ephemeral", task_id: str | None = None) -> None:
        if not self.can_execute(key):
            raise ValueError(f"idempotency key already committed: {key}")
        if self._store is not None:
            self._store.record_idempotency_key(key, scope, task_id)
        else:
            self._committed.add(key)
