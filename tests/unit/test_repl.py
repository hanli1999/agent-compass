"""REPL unit tests driven via the CompassRepl class (no subprocess)."""
import io

import pytest

from agent_compass import Compass, CompassConfig
from agent_compass.repl import CompassRepl


@pytest.fixture
def repl(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    compass = Compass(CompassConfig(data_dir=tmp_path))
    return CompassRepl(compass, format_name="text", color=False)


def test_doctor(repl):
    out = repl.run("doctor")
    assert "agent-compass" in out
    assert "0.3.0" in out


def test_decide(repl):
    out = repl.run("decide --input 'latest version' --time-sensitive")
    assert "retrieve" in out


def test_task_create_with_multiword_goal(repl):
    out = repl.run("task create demo repl session")
    assert "demo repl session" in out
    assert "created" in out or "task task_" in out


def test_task_list_after_create(repl):
    repl.run("task create hello world")
    out = repl.run("task list")
    assert "hello world" in out


def test_memory_propose_and_list(repl):
    repl.run("memory propose --content 'always test first' --type task_lesson")
    out = repl.run("memory list")
    assert "always test first" in out


def test_memory_search(repl):
    repl.run("memory propose --content 'use deterministic fixtures' --keyword test")
    repl.run("memory propose --content 'prefer offline workflows' --keyword ci")
    out = repl.run("memory search --query test")
    assert "use deterministic fixtures" in out
    assert "prefer offline workflows" not in out


def test_privacy_scan(repl):
    out = repl.run("privacy scan --text 'contact alice@example.com'")
    assert "sensitive" in out
    assert "email" in out


def test_feedback_stats(repl):
    repl.run("feedback add --signal ok --label positive --task-id t1")
    repl.run("feedback add --signal bad --label negative --task-id t1")
    out = repl.run("feedback stats --task-id t1")
    import json

    body = json.loads(out)
    assert body["by_label"]["positive"] == 1
    assert body["by_label"]["negative"] == 1


def test_unknown_command_is_friendly(repl):
    out = repl.run("nope")
    assert "unknown command" in out


def test_help(repl):
    out = repl.run("help")
    assert "decide" in out
    assert "memory" in out
    assert "exit" in out


def test_empty_line_does_nothing(repl):
    assert repl.run("") == ""


def test_keyerror_is_caught(repl):
    out = repl.run("task show missing_id")
    assert "not found" in out


def test_value_error_is_caught(repl):
    out = repl.run("task create ")
    # shlex.split on "task create " yields ['task', 'create']; do_task runs _task_create([])
    # which raises ValueError -> "error: task create requires a goal"
    assert "error" in out
