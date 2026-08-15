"""Provider-neutral models for Agent Compass."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DecisionAction(str, Enum):
    ANSWER_DIRECTLY = "answer_directly"
    RETRIEVE = "retrieve"
    ASK_USER = "ask_user"
    CONTINUE = "continue"
    PAUSE_FOR_APPROVAL = "pause_for_approval"
    RESUME = "resume"
    CONSOLIDATE_MEMORY = "consolidate_memory"
    STOP = "stop"
    # Added in policy-v3 (opt-in). Forces an outer-action loop: retrieve first,
    # then take at least one tool step before the caller is allowed to answer.
    # Hosts that do not honour this action should fall back to RETRIEVE.
    RETRIEVE_THEN_ACT = "retrieve_then_act"
    # Added in policy-v3 + web adapter (opt-in). Means "do a ReAct-style loop:
    # web_search -> inspect -> maybe web_fetch -> answer". Hosts that do not
    # implement ReAct should map EXPLORE to RETRIEVE_THEN_ACT. EXPLORE is the
    # only new action that *requires* `remote_allowed`; the other v3 branches
    # keep working offline.
    EXPLORE = "explore"


class TaskStatus(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SessionState(str, Enum):
    NEW = "new"
    ONGOING = "ongoing"
    INTERRUPTED = "interrupted"
    ENDING = "ending"
    ENDED = "ended"


@dataclass
class DecisionContext:
    user_input: str
    task_id: str | None = None
    available_tools: list[str] = field(default_factory=list)
    context_sources: list[str] = field(default_factory=list)
    has_sufficient_context: bool = False
    explicit_search_request: bool = False
    external_side_effect: bool = False
    destructive_action: bool = False
    ambiguity: float = 0.0
    time_sensitive: bool = False
    remote_allowed: bool = False
    task_in_progress: bool = False
    waiting_for_approval: bool = False
    # New in 0.2.0
    proposed_actions: list[str] = field(default_factory=list)
    retry_count: int = 0
    retry_budget: int | None = None
    session_state: SessionState = SessionState.NEW
    interrupted: bool = False
    failure_streak: int = 0
    last_error: str = ""
    # New in 0.6.0 (policy-v3, opt-in). All four default to neutral so a host
    # that never sets them keeps v2 behaviour even when v3 is enabled globally.
    complexity_score: float = 0.0          # 0..1, host's read of "is this multi-step / multi-source?"
    uncertainty_score: float = 0.0        # 0..1, host's read of "do I actually know the answer?"
    consecutive_answer_directly: int = 0  # how many ANSWER_DIRECTLY in a row, set by host loop
    recent_actions: list[str] = field(default_factory=list)  # names of recent tool calls, for "thinking without acting" detection

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["session_state"] = self.session_state.value
        return value


@dataclass
class Decision:
    action: DecisionAction
    reason_codes: list[str] = field(default_factory=list)
    confidence: float = 1.0
    requires_user: bool = False
    scope: str = "local"
    # Default stays policy-v2 so a fresh host that has not opted in keeps the
    # historical behaviour. The engine bumps this to "policy-v3" only when it
    # actually consulted a v3-only branch.
    policy_version: str = "policy-v2"
    created_at: str = field(default_factory=utc_now)
    decision_id: str = field(default_factory=lambda: f"dec_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action"] = self.action.value
        return value


@dataclass
class Task:
    goal: str
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    status: TaskStatus = TaskStatus.CREATED
    current_phase: str = ""
    completed_steps: list[str] = field(default_factory=list)
    pending_steps: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    retry_count: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class MemoryCandidate:
    content: str
    memory_type: str = "task_lesson"
    privacy: str = "local_only"
    source: str = "session"
    importance: float = 0.5
    novelty: float = 0.5
    keywords: list[str] = field(default_factory=list)
    # New in 0.2.0
    memory_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    status: MemoryStatus = MemoryStatus.CANDIDATE
    access_count: int = 0
    last_accessed: str | None = None
    related_task_id: str | None = None
    # New in 0.4.0 — only used by the activation-v2 formula. Left as None on
    # v1 records, where they contribute nothing.
    emotion_tag: str | None = None
    instinct_tag: str | None = None
    formula_version: str = "activation-v1"
    score: float | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class FeedbackEvent:
    signal: str
    label: str = "neutral"
    scope: str = "this_task"
    task_id: str | None = None
    decision_id: str | None = None
    notes: str = ""
    feedback_id: str = field(default_factory=lambda: f"fb_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
