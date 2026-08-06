"""Feedback event helpers."""
from ..models import FeedbackEvent
from ..storage.sqlite import SQLiteStore


def record_feedback(store: SQLiteStore, signal: str, *, label: str = "neutral", scope: str = "this_task", task_id: str | None = None, decision_id: str | None = None, notes: str = "") -> FeedbackEvent:
    event = FeedbackEvent(signal=signal, label=label, scope=scope, task_id=task_id, decision_id=decision_id, notes=notes)
    store.save_feedback(event.to_dict())
    return event
