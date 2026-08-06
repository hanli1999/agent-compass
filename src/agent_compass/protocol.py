"""JSONL protocol dispatcher used by the ``serve`` CLI command."""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import Compass
from .models import (
    DecisionContext,
    FeedbackEvent,
    MemoryCandidate,
    MemoryStatus,
    SessionState,
    Task,
    TaskStatus,
    utc_now,
)
from .privacy.boundary import PrivacyBoundary


_SUPPORTED: dict[str, str] = {
    "decision.request": "decision.response",
    "task.create": "task.create.response",
    "task.show": "task.show.response",
    "task.list": "task.list.response",
    "task.advance": "task.advance.response",
    "task.checkpoint": "task.checkpoint.response",
    "task.resume": "task.resume.response",
    "memory.propose": "memory.propose.response",
    "memory.list": "memory.list.response",
    "memory.touch": "memory.touch.response",
    "memory.archive": "memory.archive.response",
    "memory.delete": "memory.delete.response",
    "memory.prune": "memory.prune.response",
    "memory.search": "memory.search.response",
    "privacy.scan": "privacy.scan.response",
    "feedback.record": "feedback.record.response",
    "feedback.list": "feedback.list.response",
    "feedback.stats": "feedback.stats.response",
    "idempotency.commit": "idempotency.commit.response",
    "task.delete": "task.delete.response",
    "doctor": "doctor.response",
}


def _decision(compass: Compass, payload: dict) -> dict:
    return compass.decide(DecisionContext(**payload)).to_dict()


def _task_create(compass: Compass, payload: dict) -> dict:
    goal = payload.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task.create requires a non-empty 'goal'")
    metadata = {k: v for k, v in payload.items() if k != "goal"}
    return compass.tasks.create(goal, **metadata).to_dict()


def _task_show(compass: Compass, payload: dict) -> dict:
    task_id = payload.get("task_id")
    if not task_id:
        raise ValueError("task.show requires 'task_id'")
    value = compass.tasks.get(task_id)
    if value is None:
        raise KeyError(task_id)
    return value


def _task_list(compass: Compass, payload: dict) -> dict:
    return {"tasks": compass.tasks.list(limit=int(payload.get("limit", 50)))}


def _task_advance(compass: Compass, payload: dict) -> dict:
    task_id = payload.get("task_id")
    if not task_id:
        raise ValueError("task.advance requires 'task_id'")
    return compass.tasks.advance(
        task_id,
        target=payload.get("target"),
        completed_step=payload.get("completed_step"),
        reason=payload.get("reason", ""),
    )


def _task_checkpoint(compass: Compass, payload: dict) -> dict:
    task_id = payload.get("task_id")
    phase = payload.get("phase")
    if not task_id or not phase:
        raise ValueError("task.checkpoint requires 'task_id' and 'phase'")
    return compass.tasks.checkpoint(
        task_id,
        phase,
        completed_steps=payload.get("completed_steps", []),
        pending_steps=payload.get("pending_steps", []),
        notes=payload.get("notes", []),
        artifacts=payload.get("artifacts", []),
    )


def _task_resume(compass: Compass, payload: dict) -> dict:
    task_id = payload.get("task_id")
    if not task_id:
        raise ValueError("task.resume requires 'task_id'")
    return compass.tasks.resume(task_id)


def _memory_propose(compass: Compass, payload: dict) -> dict:
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValueError("memory.propose requires 'content'")
    return compass.memory.propose(
        content,
        memory_type=payload.get("memory_type", "task_lesson"),
        privacy=payload.get("privacy"),
        keywords=payload.get("keywords", []),
        importance=payload.get("importance"),
        novelty=payload.get("novelty", 0.5),
        source=payload.get("source", "session"),
        related_task_id=payload.get("related_task_id"),
    ).to_dict()


def _memory_list(compass: Compass, payload: dict) -> dict:
    status = payload.get("status")
    status_value = MemoryStatus(status) if status else None
    items = compass.memory.list(
        status=status_value,
        privacy=payload.get("privacy"),
        limit=int(payload.get("limit", 50)),
    )
    return {"memories": items}


def _memory_touch(compass: Compass, payload: dict) -> dict:
    memory_id = payload.get("memory_id")
    if not memory_id:
        raise ValueError("memory.touch requires 'memory_id'")
    return compass.memory.touch(memory_id).to_dict()


def _memory_archive(compass: Compass, payload: dict) -> dict:
    memory_id = payload.get("memory_id")
    if not memory_id:
        raise ValueError("memory.archive requires 'memory_id'")
    return compass.memory.archive(memory_id).to_dict()


def _memory_delete(compass: Compass, payload: dict) -> dict:
    memory_id = payload.get("memory_id")
    if not memory_id:
        raise ValueError("memory.delete requires 'memory_id'")
    return {"deleted": compass.memory.delete(memory_id), "memory_id": memory_id}


def _memory_prune(compass: Compass, payload: dict) -> dict:
    return compass.memory.prune(
        below=float(payload.get("below", 0.15)),
        stale_below=float(payload.get("stale_below", 0.3)),
        dry_run=bool(payload.get("dry_run", False)),
    )


def _memory_search(compass: Compass, payload: dict) -> dict:
    from .models import MemoryStatus

    status = payload.get("status")
    status_value = MemoryStatus(status) if status else None
    items = compass.memory.search(
        query=payload.get("query"),
        memory_type=payload.get("memory_type"),
        min_score=payload.get("min_score"),
        status=status_value,
        privacy=payload.get("privacy"),
        limit=int(payload.get("limit", 50)),
    )
    return {"memories": items}


def _task_delete(compass: Compass, payload: dict) -> dict:
    task_id = payload.get("task_id")
    if not task_id:
        raise ValueError("task.delete requires 'task_id'")
    return compass.tasks.delete(task_id, soft=bool(payload.get("soft", False)))


def _privacy_scan(_compass: Compass, payload: dict) -> dict:
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise ValueError("privacy.scan requires 'text'")
    boundary = PrivacyBoundary()
    inspection = boundary.inspect(text)
    return {
        "level": inspection.level.name.lower(),
        "matches": list(inspection.matches),
        "blocked": inspection.blocked,
        "redacted": boundary.redact(text) if inspection.matches else text,
    }


def _feedback_record(compass: Compass, payload: dict) -> dict:
    signal = payload.get("signal")
    if not signal:
        raise ValueError("feedback.record requires 'signal'")
    return compass.feedback.record(
        signal,
        label=payload.get("label", "neutral"),
        scope=payload.get("scope", "this_task"),
        task_id=payload.get("task_id"),
        decision_id=payload.get("decision_id"),
        notes=payload.get("notes", ""),
    ).to_dict()


def _feedback_list(compass: Compass, payload: dict) -> dict:
    return {"feedback": compass.feedback.list(task_id=payload.get("task_id"), limit=int(payload.get("limit", 50)))}


def _feedback_stats(compass: Compass, payload: dict) -> dict:
    return compass.feedback.stats(task_id=payload.get("task_id"))


def _idempotency_commit(compass: Compass, payload: dict) -> dict:
    key = payload.get("key")
    if not key:
        raise ValueError("idempotency.commit requires 'key'")
    compass.idempotency.commit(
        key,
        scope=payload.get("scope", "ephemeral"),
        task_id=payload.get("task_id"),
    )
    return {"key": key, "committed": True, "committed_at": utc_now()}


def _doctor(compass: Compass, _payload: dict) -> dict:
    return {
        "ok": True,
        "version": "0.2.0",
        "policy_version": "policy-v2",
        "data_dir": str(compass.config.data_dir),
        "schema_version": compass.store.schema_version(),
    }


_HANDLERS: dict[str, Callable[[Compass, dict], Any]] = {
    "decision.request": _decision,
    "task.create": _task_create,
    "task.show": _task_show,
    "task.list": _task_list,
    "task.advance": _task_advance,
    "task.checkpoint": _task_checkpoint,
    "task.resume": _task_resume,
    "memory.propose": _memory_propose,
    "memory.list": _memory_list,
    "memory.touch": _memory_touch,
    "memory.archive": _memory_archive,
    "memory.delete": _memory_delete,
    "memory.prune": _memory_prune,
    "memory.search": _memory_search,
    "privacy.scan": _privacy_scan,
    "feedback.record": _feedback_record,
    "feedback.list": _feedback_list,
    "feedback.stats": _feedback_stats,
    "idempotency.commit": _idempotency_commit,
    "task.delete": _task_delete,
    "doctor": _doctor,
}


def handle(compass: Compass, request: dict) -> dict:
    """Dispatch a single JSONL request and return a response dict."""
    request_id = request.get("request_id")
    kind = request.get("type")
    payload = request.get("payload") or {}
    response_type = _SUPPORTED.get(kind)
    if response_type is None:
        return {
            "type": "error",
            "request_id": request_id,
            "payload": {"code": "unsupported_request", "got": kind, "supported": sorted(_SUPPORTED)},
        }
    handler = _HANDLERS[kind]
    try:
        result = handler(compass, payload)
        return {"type": response_type, "request_id": request_id, "payload": result}
    except KeyError as exc:
        return {
            "type": "error",
            "request_id": request_id,
            "payload": {"code": "not_found", "message": str(exc)},
        }
    except ValueError as exc:
        return {
            "type": "error",
            "request_id": request_id,
            "payload": {"code": "invalid_request", "message": str(exc)},
        }


def serve(stdin=None, stdout=None) -> int:
    """Read JSONL requests from stdin and write responses to stdout."""
    compass = Compass.from_config()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            stdout.write(json.dumps({"type": "error", "payload": {"code": "invalid_json", "message": str(exc)}}, ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        response = handle(compass, request)
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0
