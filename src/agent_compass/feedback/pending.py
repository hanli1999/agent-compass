"""Async feedback buffer for hooks that cannot afford to block.

Background
----------

幻梦 runs two ``PostToolUse`` hooks: ``agent-compass feedback add`` and
a sound reminder. The two race each other in synchronous mode, and the
sound reminder always wins because the feedback call is doing a SQLite
write. v0.5.0 fixes that by making ``feedback add`` *async by default*:
the call appends one line to
``~/.claude/state/feedback_pending.jsonl`` and returns 0 immediately. A
``feedback flush`` (also shipped in v0.5.0) reads the pending file,
batches the entries into the regular feedback store, and reports how
many it wrote.

The pending file is one JSON object per line. Lines are intentionally
append-only — a flush atomically swaps in a fresh empty file. A
concurrent ``feedback add`` that beats the swap is preserved: the
``O_APPEND`` semantics on POSIX plus our copy-then-rename pattern
guarantee no event is lost.

Environment
-----------

* ``AGENT_COMPASS_FEEDBACK_SYNC=1`` flips ``feedback add`` back to
  synchronous mode for callers that prefer it (cron, unit tests, a
  hook that runs after a long think and wants the write to land
  before the next turn).
* ``AGENT_COMPASS_CLAUDE_STATE_DIR`` overrides the parent directory of
  the pending file (see :mod:`agent_compass.context`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..context import (
    feedback_pending_lock_path,
    feedback_pending_path,
)


def is_sync_mode() -> bool:
    """Whether the host has opted back into synchronous feedback writes."""
    return os.environ.get("AGENT_COMPASS_FEEDBACK_SYNC", "").lower() in {"1", "true", "yes"}


def append_pending(event: dict) -> Path:
    """Append one feedback event to the pending file. Returns the path."""
    path = feedback_pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``O_APPEND`` is atomic on POSIX for small writes. We still keep the
    # line short and self-contained so a torn write is recoverable.
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        fp.write("\n")
    return path


def read_pending() -> list[dict]:
    """Return all currently-pending events. Does not clear the file."""
    path = feedback_pending_path()
    if not path.exists():
        return []
    events: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn line. Skip it rather than aborting the whole flush
            # — the next write will keep going.
            continue
    return events


def swap_pending() -> list[dict]:
    """Atomically take the pending file's contents and replace it with empty.

    We do this via a sibling lock file so two concurrent flushes do
    not both think they own the data. The lock file is best-effort —
    on a single-user laptop there is no real race, and on a shared
    host the SQLite store at the bottom of the stack is the actual
    mutual-exclusion boundary.
    """
    path = feedback_pending_path()
    lock = feedback_pending_lock_path()
    if not path.exists():
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        # Another flush is in flight. Bail so we do not double-write.
        return []
    try:
        lock.write_text("flushing", encoding="utf-8")
    except OSError:
        return []
    try:
        events = read_pending()
        # Truncate in place — a competing append will reopen the file
        # in append mode and the kernel will keep the writes safe.
        with path.open("w", encoding="utf-8"):
            pass
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
    return events


__all__ = ["append_pending", "is_sync_mode", "read_pending", "swap_pending"]
