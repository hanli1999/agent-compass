"""Hook-context state: the small "current task" pointer a hook needs.

Claude Code does not expose a "current task" concept. A ``Stop`` hook
that wants to checkpoint the active task therefore has no way to
discover which ``task_id`` to use. This module is the answer.

Three places hold the answer, in priority order:

1. An explicit ``--unspecified`` flag or ``task_id`` arg on the CLI.
2. The state file at ``~/.claude/state/last_task_id``. The
   ``UserPromptSubmit`` hook writes it via :func:`set_last_task_id`
   whenever a new task is created.
3. The ``AGENT_COMPASS_TASK_ID`` env var. Set by the user / an outer
   orchestrator when state-file resolution is not available (e.g. a
   CI environment, a fresh shell with no history).

Falling back to "unspecified" is a real last resort and is logged as
a warning. The goal is for hooks to bind to a known task so the
checkpoint is recoverable; an unspecified task is the same as not
checkpointing at all, but it lets the rest of the system keep
running.

The state directory is intentionally *outside* ``data_dir`` so a
host that moves or wipes its memory store does not lose the
``last_task_id`` pointer. The pointer is metadata about the host
session, not project state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The directory the state files live in. Honour an override via env so a
#: containerised Claude Code can point at a different root.
CLAUDE_STATE_DIR_ENV = "AGENT_COMPASS_CLAUDE_STATE_DIR"

#: File that holds the most recent task id. A ``UserPromptSubmit`` hook
#: writes it; a ``Stop`` hook reads it. The file is intentionally plain
#: text — a single line, a single id — so a human can ``cat`` it and
#: a hook can ``read_text().strip()`` it.
LAST_TASK_ID_FILE = "last_task_id"

#: File that the async ``feedback add`` writes to. Read by
#: ``agent-compass feedback flush`` and persisted to SQLite.
FEEDBACK_PENDING_FILE = "feedback_pending.jsonl"

#: File that ``feedback flush`` moves the pending file to while
#: reading it, so a concurrent ``feedback add`` does not race against
#: the flush.
FEEDBACK_PENDING_LOCK_FILE = "feedback_pending.lock"


def state_dir() -> Path:
    """The directory the state files live in. Created on first write."""
    override = os.environ.get(CLAUDE_STATE_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".claude" / "state"


def last_task_id_path() -> Path:
    return state_dir() / LAST_TASK_ID_FILE


def feedback_pending_path() -> Path:
    return state_dir() / FEEDBACK_PENDING_FILE


def feedback_pending_lock_path() -> Path:
    return state_dir() / FEEDBACK_PENDING_LOCK_FILE


@dataclass(frozen=True)
class ContextResolution:
    """The outcome of asking "what is the current task?".

    ``source`` is one of ``"explicit"``, ``"state_file"``, ``"env"``,
    ``"unspecified"``. Callers can use the source to decide whether
    to log a warning (everything except ``"explicit"`` is at least
    slightly suspicious).
    """

    task_id: str
    source: str


def set_last_task_id(task_id: str) -> Path:
    """Write the pointer file. Returns the path that was written."""
    path = last_task_id_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(task_id, encoding="utf-8")
    return path


def clear_last_task_id() -> bool:
    """Remove the pointer file. Returns True if anything was removed."""
    path = last_task_id_path()
    if path.exists():
        path.unlink()
        return True
    return False


def get_last_task_id() -> str | None:
    """Read the pointer file. Returns None if it is missing or empty."""
    path = last_task_id_path()
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def resolve_task_id(
    *,
    explicit: str | None = None,
    unspecified: bool = False,
) -> ContextResolution:
    """Resolve a task id for a hook using the documented priority order.

    Parameters
    ----------
    explicit:
        The task id the caller already knows (a CLI ``task_id`` arg).
        Wins over every other source.
    unspecified:
        When True, the caller is *asking* for resolution rather than
        passing a known id. We then walk the priority chain: state
        file → env var → literal ``"unspecified"``. A literal
        ``"unspecified"`` return is the genuine "I have no business
        checkpointing a particular task" path; the caller is expected
        to log a warning when the source ends up there.

    The flag is what makes the fallback *deliberate*. With
    ``unspecified=False`` and no explicit id the caller is expected
    to have errored upstream rather than asking us to pick.
    """
    if explicit:
        return ContextResolution(task_id=explicit, source="explicit")
    if not unspecified:
        # Without the flag the caller is asserting they have a task id.
        # If they did not provide one, error upstream. We still try the
        # state file as a courtesy so a hook that forgot the flag still
        # works, but we *do not* return a placeholder id.
        state = get_last_task_id()
        if state:
            return ContextResolution(task_id=state, source="state_file")
        env = os.environ.get("AGENT_COMPASS_TASK_ID", "").strip()
        if env:
            return ContextResolution(task_id=env, source="env")
        return ContextResolution(task_id="", source="missing")
    state = get_last_task_id()
    if state:
        return ContextResolution(task_id=state, source="state_file")
    env = os.environ.get("AGENT_COMPASS_TASK_ID", "").strip()
    if env:
        return ContextResolution(task_id=env, source="env")
    return ContextResolution(task_id="unspecified", source="unspecified")


#: Reason codes that travel on a Decision so a caller can confirm its
#: own bookkeeping without a separate round-trip.
LAST_TASK_ID_REASON = "last_task_id"

__all__ = [
    "CLAUDE_STATE_DIR_ENV",
    "ContextResolution",
    "FEEDBACK_PENDING_FILE",
    "FEEDBACK_PENDING_LOCK_FILE",
    "LAST_TASK_ID_FILE",
    "LAST_TASK_ID_REASON",
    "clear_last_task_id",
    "feedback_pending_lock_path",
    "feedback_pending_path",
    "get_last_task_id",
    "last_task_id_path",
    "resolve_task_id",
    "set_last_task_id",
    "state_dir",
]
