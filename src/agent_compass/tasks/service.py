"""Task service: persistence, transitions, checkpoints, feedback."""
from __future__ import annotations

from typing import Any

from ..models import FeedbackEvent, Task, TaskStatus, utc_now
from .state_machine import Checkpoint, TaskStateMachine


def _hydrate(record: dict) -> Task:
    """Rebuild a Task from a stored dict, restoring enum types."""
    fields = {k: v for k, v in record.items() if k in Task.__dataclass_fields__}
    fields["status"] = TaskStatus(fields["status"])
    return Task(**fields)


class TaskService:
    def __init__(self, store):
        self.store = store
        self.machine = TaskStateMachine()

    def create(self, goal: str, **metadata) -> Task:
        task = Task(goal=goal, metadata=dict(metadata))
        self.store.save_task(task)
        return task

    def get(self, task_id: str) -> dict | None:
        return self.store.get_task(task_id)

    def list(self, limit: int = 50) -> list[dict]:
        return self.store.list_tasks(limit=limit)

    def transition(
        self,
        task_id: str,
        target: TaskStatus | str,
        *,
        reason: str = "",
    ) -> dict:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(task_id)
        task = _hydrate(record)
        target_status = TaskStatus(target) if not isinstance(target, TaskStatus) else target
        self.machine.transition(task, target_status, reason=reason)
        self.store.save_task(task)
        return task.to_dict()

    def advance(
        self,
        task_id: str,
        *,
        target: TaskStatus | str | None = None,
        completed_step: str | None = None,
        reason: str = "",
    ) -> dict:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(task_id)
        task = _hydrate(record)
        if completed_step:
            if completed_step not in task.completed_steps:
                task.completed_steps.append(completed_step)
            if completed_step in task.pending_steps:
                task.pending_steps.remove(completed_step)
        if target is not None:
            target_status = TaskStatus(target) if not isinstance(target, TaskStatus) else target
            self.machine.transition(task, target_status, reason=reason)
        task.updated_at = utc_now()
        self.store.save_task(task)
        return task.to_dict()

    def checkpoint(
        self,
        task_id: str,
        phase: str,
        completed_steps: list[str] | None = None,
        pending_steps: list[str] | None = None,
        notes: list[str] | None = None,
        artifacts: list[str] | None = None,
    ) -> dict:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(task_id)
        task = _hydrate(record)
        ck = Checkpoint(
            task_id=task_id,
            phase=phase,
            completed_steps=list(completed_steps or []),
            pending_steps=list(pending_steps or task.pending_steps or []),
            notes=list(notes or []),
            artifacts=list(artifacts or []),
        )
        self.machine.checkpoint(task, ck)
        self.store.save_task(task)
        self.store.save_checkpoint(task_id, phase, ck.__dict__)
        return task.to_dict()

    def resume(self, task_id: str) -> dict:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(task_id)
        task = _hydrate(record)
        result = self.machine.resume(task)
        if not result.get("checkpoint"):
            latest = self.store.latest_checkpoint(task_id)
            if latest:
                result["checkpoint"] = latest
        return result


class FeedbackService:
    def __init__(self, store):
        self.store = store

    def record(
        self,
        signal: str,
        *,
        label: str = "neutral",
        scope: str = "this_task",
        task_id: str | None = None,
        decision_id: str | None = None,
        notes: str = "",
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=signal,
            label=label,
            scope=scope,
            task_id=task_id,
            decision_id=decision_id,
            notes=notes,
        )
        self.store.save_feedback(event.to_dict())
        return event

    def list(self, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_feedback(task_id=task_id, limit=limit)
