"""Tests for the v0.5.0 hooks-completion work.

Three pieces land together:

1. ``agent_compass.context`` — a small "current task" pointer persisted
   in ``~/.claude/state/last_task_id`` (or wherever
   ``AGENT_COMPASS_CLAUDE_STATE_DIR`` points). UserPromptSubmit writes
   it; Stop reads it.
2. ``task checkpoint --unspecified`` — the Stop hook flags. Resolves
   the task id from the state file, then the env var, then falls back
   to the literal string ``"unspecified"`` with a stderr warning.
3. Async ``feedback add`` + ``feedback flush`` — feedback events go to
   ``feedback_pending.jsonl`` by default, return 0 immediately, and
   are persisted by an explicit ``feedback flush`` call (or the
   ``AGENT_COMPASS_FEEDBACK_SYNC=1`` opt-out).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_compass.context import (
    clear_last_task_id,
    get_last_task_id,
    resolve_task_id,
    set_last_task_id,
    state_dir,
)
from agent_compass.feedback.pending import (
    append_pending,
    is_sync_mode,
    read_pending,
    swap_pending,
)


ROOT = Path(__file__).resolve().parents[2]


# ---- context module -----------------------------------------------------


def test_set_and_get_last_task_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    assert get_last_task_id() is None
    set_last_task_id("task_abc123")
    assert get_last_task_id() == "task_abc123"
    assert (tmp_path / "last_task_id").read_text(encoding="utf-8") == "task_abc123"


def test_clear_last_task_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    set_last_task_id("task_abc")
    assert clear_last_task_id() is True
    assert get_last_task_id() is None
    assert clear_last_task_id() is False  # already cleared


def test_resolve_priority_order_explicit_first(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_COMPASS_TASK_ID", raising=False)
    set_last_task_id("task_state")
    resolution = resolve_task_id(explicit="task_cli")
    assert resolution.task_id == "task_cli"
    assert resolution.source == "explicit"


def test_resolve_priority_order_state_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_COMPASS_TASK_ID", raising=False)
    set_last_task_id("task_state")
    resolution = resolve_task_id()
    assert resolution.task_id == "task_state"
    assert resolution.source == "state_file"


def test_resolve_priority_order_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_COMPASS_TASK_ID", "task_env")
    resolution = resolve_task_id()
    assert resolution.task_id == "task_env"
    assert resolution.source == "env"


def test_resolve_priority_order_unspecified_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_COMPASS_TASK_ID", raising=False)
    # Without ``unspecified=True`` and without any lookup hit, the
    # resolver returns a "missing" marker so the CLI can fail loudly
    # rather than silently inventing a task id.
    resolution = resolve_task_id()
    assert resolution.task_id == ""
    assert resolution.source == "missing"


def test_resolve_unspecified_flag_falls_back_to_literal(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_COMPASS_TASK_ID", raising=False)
    # No state file, no env var: the documented fallback returns the
    # literal "unspecified" string so a Stop hook can still log and exit
    # rather than crashing.
    resolution = resolve_task_id(unspecified=True)
    assert resolution.task_id == "unspecified"
    assert resolution.source == "unspecified"


def test_resolve_unspecified_flag_prefers_state_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    set_last_task_id("task_state")
    resolution = resolve_task_id(unspecified=True)
    assert resolution.task_id == "task_state"
    assert resolution.source == "state_file"


# ---- async feedback ----------------------------------------------------


def test_is_sync_mode_default_off(monkeypatch):
    monkeypatch.delenv("AGENT_COMPASS_FEEDBACK_SYNC", raising=False)
    assert is_sync_mode() is False


def test_is_sync_mode_honours_env(monkeypatch):
    monkeypatch.setenv("AGENT_COMPASS_FEEDBACK_SYNC", "1")
    assert is_sync_mode() is True
    monkeypatch.setenv("AGENT_COMPASS_FEEDBACK_SYNC", "true")
    assert is_sync_mode() is True


def test_append_pending_creates_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    append_pending({"signal": "ok", "label": "positive"})
    append_pending({"signal": "bad", "label": "negative"})
    events = read_pending()
    assert [e["signal"] for e in events] == ["ok", "bad"]


def test_swap_pending_returns_events_and_clears(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_COMPASS_CLAUDE_STATE_DIR", str(tmp_path))
    append_pending({"signal": "ok", "label": "positive"})
    events = swap_pending()
    assert len(events) == 1
    assert events[0]["signal"] == "ok"
    # After swap, the file is empty and a second swap returns nothing.
    assert read_pending() == []
    assert swap_pending() == []


# ---- CLI integration ---------------------------------------------------


def _run(args, tmp_path, env_overrides=None):
    env = {
        **os.environ,
        "AGENT_COMPASS_DATA_DIR": str(tmp_path),
        "AGENT_COMPASS_CLAUDE_STATE_DIR": str(tmp_path / "state"),
        "PYTHONPATH": str(ROOT / "src"),
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "agent_compass.cli", *args],
        capture_output=True, text=True, env=env,
    )


def test_cli_context_set_show_clear(tmp_path):
    write = _run(["--format", "json", "context", "set", "--task-id", "task_xyz"], tmp_path)
    assert write.returncode == 0
    body = json.loads(write.stdout)
    assert body["task_id"] == "task_xyz"
    assert body["path"].endswith("last_task_id")

    show = _run(["--format", "json", "context", "show"], tmp_path)
    assert show.returncode == 0
    assert json.loads(show.stdout) == {"task_id": "task_xyz"}

    clear = _run(["--format", "json", "context", "clear"], tmp_path)
    assert clear.returncode == 0
    assert json.loads(clear.stdout) == {"cleared": True}

    missing = _run(["--format", "json", "context", "show"], tmp_path)
    assert missing.returncode == 1
    assert json.loads(missing.stdout) == {"task_id": None}


def test_cli_checkpoint_unspecified_resolves_state_file(tmp_path):
    # Create a real task, set it as current, then checkpoint with --unspecified.
    create = _run(["--format", "json", "task", "create", "run the v0.5.0 work"], tmp_path)
    assert create.returncode == 0
    task_id = json.loads(create.stdout)["task_id"]
    _run(["--format", "json", "context", "set", "--task-id", task_id], tmp_path)
    result = _run(["--format", "json", "task", "checkpoint", "--unspecified", "final",
                   "--note", "session ended"], tmp_path)
    assert result.returncode == 0
    assert task_id in result.stdout


def test_cli_checkpoint_explicit_arg_wins_over_state(tmp_path):
    # Create two tasks; set one as current; checkpoint the other explicitly.
    a = _run(["--format", "json", "task", "create", "task a"], tmp_path)
    b = _run(["--format", "json", "task", "create", "task b"], tmp_path)
    assert a.returncode == 0 and b.returncode == 0
    a_id = json.loads(a.stdout)["task_id"]
    b_id = json.loads(b.stdout)["task_id"]
    _run(["--format", "json", "context", "set", "--task-id", a_id], tmp_path)
    result = _run(["--format", "json", "task", "checkpoint", b_id, "final"], tmp_path)
    assert result.returncode == 0
    assert b_id in result.stdout
    assert a_id not in result.stdout


def test_cli_checkpoint_unspecified_fallback_warns_on_stderr(tmp_path):
    # No context, no env var. With --unspecified the CLI resolves to
    # the literal "unspecified" task id, logs a warning on stderr, and
    # creates the placeholder task on the fly so the checkpoint can
    # land. The store is happy to create tasks on demand.
    result = _run(["--format", "json", "task", "checkpoint", "--unspecified", "final"], tmp_path)
    assert result.returncode == 0
    assert "warning" in result.stderr
    # The warning includes the literal "unspecified" task id; the
    # check on stdout catches the case where the same text bleeds into
    # the rendered task payload (it does, via the task_id_source field).
    combined = (result.stdout or "") + (result.stderr or "")
    assert "unspecified" in combined


def test_cli_checkpoint_missing_errors(tmp_path):
    # Without a task_id arg, without --unspecified, and without any
    # resolution hit, the CLI must fail loudly (returncode != 0) rather
    # than silently inventing a task id.
    result = _run(["--format", "json", "task", "checkpoint", "final"], tmp_path)
    assert result.returncode != 0
    assert "error" in result.stderr
    assert "task_id" in result.stderr or "--unspecified" in result.stderr


def test_cli_feedback_add_async_writes_to_pending_file(tmp_path):
    result = _run(["--format", "json", "feedback", "add", "--signal", "ok"], tmp_path)
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["queued"] is True
    pending_path = Path(body["pending_file"])
    assert pending_path.exists()
    lines = [line for line in pending_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["signal"] == "ok"


def test_cli_feedback_flush_persists_pending(tmp_path):
    _run(["--format", "json", "feedback", "add", "--signal", "ok", "--label", "positive"], tmp_path)
    _run(["--format", "json", "feedback", "add", "--signal", "bad", "--label", "negative"], tmp_path)

    flush = _run(["--format", "json", "feedback", "flush"], tmp_path)
    assert flush.returncode == 0
    summary = json.loads(flush.stdout)
    assert summary["flushed"] == 2
    assert summary["errors"] == []

    # The pending file is now empty.
    pending = next(Path(tmp_path).rglob("feedback_pending.jsonl"))
    assert pending.read_text(encoding="utf-8") == ""

    # Stats should now see both events.
    stats = _run(["--format", "json", "feedback", "stats"], tmp_path)
    assert json.loads(stats.stdout)["total"] == 2


def test_cli_feedback_add_sync_flag_persists_immediately(tmp_path):
    result = _run(["--format", "json", "feedback", "add", "--sync", "--signal", "ok"], tmp_path)
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert "feedback_id" in body  # synchronous path returns the saved event
    stats = _run(["--format", "json", "feedback", "stats"], tmp_path)
    assert json.loads(stats.stdout)["total"] == 1


def test_cli_feedback_add_sync_via_env(tmp_path):
    result = _run(
        ["--format", "json", "feedback", "add", "--signal", "ok"],
        tmp_path,
        env_overrides={"AGENT_COMPASS_FEEDBACK_SYNC": "1"},
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert "feedback_id" in body  # env var forces sync
