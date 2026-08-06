"""Task state machine, checkpoints, and approval semantics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Task, TaskStatus, utc_now

_ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.PLANNED, TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.PLANNED: {TaskStatus.RUNNING, TaskStatus.WAITING_FOR_USER, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.WAITING_FOR_USER, TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.WAITING_FOR_USER: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.WAITING_FOR_APPROVAL: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: set(),
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


class IdempotencyRegistry:
    def __init__(self) -> None:
        self._committed: set[str] = set()

    def can_execute(self, key: str) -> bool:
        return key not in self._committed

    def commit(self, key: str) -> None:
        if key in self._committed:
            raise ValueError(f"idempotency key already committed: {key}")
        self._committed.add(key)
