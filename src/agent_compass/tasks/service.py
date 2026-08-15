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

    def list(self, limit: int = 50, include_archived: bool = False) -> list[dict]:
        return self.store.list_tasks(limit=limit, include_archived=include_archived)

    def delete(self, task_id: str, *, soft: bool = False) -> dict:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(task_id)
        if not soft:
            if not self.store.delete_task(task_id):
                raise KeyError(task_id)
            return {"deleted": True, "task_id": task_id, "soft": False}
        task = _hydrate(record)
        self.machine.transition(task, TaskStatus.ARCHIVED, reason="soft_delete")
        self.store.save_task(task)
        return {"deleted": True, "task_id": task_id, "soft": True, "status": task.status.value}

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

    def checkpoint_or_create(
        self,
        task_id: str,
        phase: str,
        *,
        fallback_goal: str = "unspecified task (auto-created by checkpoint)",
        completed_steps: list[str] | None = None,
        pending_steps: list[str] | None = None,
        notes: list[str] | None = None,
        artifacts: list[str] | None = None,
    ) -> tuple[dict, bool]:
        """Like :meth:`checkpoint` but creates a placeholder task first when needed.

        Returns ``(task_dict, created)`` so a caller can tell whether a
        new task was born. The motivation is the ``Stop`` hook: when
        the resolution chain lands on the literal ``"unspecified"`` id
        (no state file, no env var), the hook still needs to record
        *something*. Crashing the host because there is no task to
        checkpoint is the wrong default — a placeholder is more useful
        than a stack trace.

        The created task gets a fresh ``task_xxxxx`` id from the
        store, *not* the placeholder id the caller passed. The
        returned ``created=True`` flag lets the caller log the
        mismatch.
        """
        record = self.store.get_task(task_id)
        if record is None:
            new_task = self.create(fallback_goal)
            return (
                self.checkpoint(
                    new_task.task_id,
                    phase,
                    completed_steps=completed_steps,
                    pending_steps=pending_steps,
                    notes=notes,
                    artifacts=artifacts,
                ),
                True,
            )
        return (
            self.checkpoint(
                task_id,
                phase,
                completed_steps=completed_steps,
                pending_steps=pending_steps,
                notes=notes,
                artifacts=artifacts,
            ),
            False,
        )

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

    def stats(self, task_id: str | None = None) -> dict[str, Any]:
        events = self.store.list_feedback(task_id=task_id, limit=10_000)
        by_label: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}
        by_scope: dict[str, int] = {}
        for event in events:
            label = event.get("label", "neutral")
            by_label[label] = by_label.get(label, 0) + 1
            scope = event.get("scope", "this_task")
            by_scope[scope] = by_scope.get(scope, 0) + 1
        return {
            "total": len(events),
            "by_label": by_label,
            "by_scope": by_scope,
            "task_id": task_id,
        }

    def flush_pending(self) -> dict[str, Any]:
        """Persist all queued async feedback events.

        Returns a summary dict ``{"flushed": N, "errors": [...]}`` so a
        caller (hook, cron, or the operator running this by hand) can
        confirm the queue is empty. The function is safe to call when
        the queue is empty — it returns ``{"flushed": 0, "errors": []}``.
        """
        from ..feedback.pending import swap_pending
        from ..models import FeedbackEvent

        events = swap_pending()
        errors: list[str] = []
        for entry in events:
            try:
                event = FeedbackEvent(
                    signal=entry.get("signal", "ok"),
                    label=entry.get("label", "neutral"),
                    scope=entry.get("scope", "this_task"),
                    task_id=entry.get("task_id"),
                    decision_id=entry.get("decision_id"),
                    notes=entry.get("notes", ""),
                )
            except (TypeError, ValueError) as exc:
                errors.append(f"malformed_event: {exc}")
                continue
            self.store.save_feedback(event.to_dict())
        return {"flushed": len(events) - len(errors), "errors": errors, "considered": len(events)}
