"""REPL unit tests driven via the CompassRepl class (no subprocess)."""
import io

import pytest

from agent_compass import __version__
from agent_compass import Compass, CompassConfig
from agent_compass.repl import CompassRepl


@pytest.fixture
def repl(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    compass = Compass(CompassConfig(data_dir=tmp_path))
    return CompassRepl(compass, format_name="text", color=False)


@pytest.fixture
def v3_repl(tmp_path, monkeypatch):
    """v3-enabled REPL with smart defaults applied."""
    from agent_compass.runtime import apply_smart_defaults, build_smart_default_config

    monkeypatch.setenv("NO_COLOR", "1")
    compass = Compass(
        build_smart_default_config(data_dir=tmp_path, remote_allowed=True)
    )
    apply_smart_defaults(compass)
    return CompassRepl(compass, format_name="text", color=False)


def test_doctor(repl):
    out = repl.run("doctor")
    assert "agent-compass" in out
    assert __version__ in out


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


# ---- v3 REPL commands ----------------------------------------------------


def test_v2_repl_state_returns_friendly_error(repl):
    """v3 commands in a v2 REPL session return a friendly hint instead
    of crashing — v2 hosts should not see v3 surface at all."""
    out = repl.run("state")
    assert "v3 not enabled" in out


def test_v3_repl_state_shows_tracker_snapshot(v3_repl):
    import json

    out = v3_repl.run("state")
    payload = json.loads(out)
    assert "tracker" in payload
    assert payload["tracker"]["consecutive_answer_directly"] == 0
    assert payload["tracker"]["recent_actions"] == []


def test_v3_repl_record_increments_silence(v3_repl):
    """Three 'record answer' calls should make the next decide fire
    action_pressure (the v3 silent-thinking branch)."""
    v3_repl.run("record answer")
    v3_repl.run("record answer")
    v3_repl.run("record answer")
    out = v3_repl.run("decide --input 'what now'")
    assert "retrieve_then_act" in out
    assert "action_pressure" in out


def test_v3_repl_record_tool_action(v3_repl):
    """record <tool-name> resets the silence counter."""
    v3_repl.run("record answer")
    v3_repl.run("record answer")
    v3_repl.run("record retrieve")
    out = v3_repl.run("state")
    import json

    payload = json.loads(out)
    assert payload["tracker"]["consecutive_answer_directly"] == 0
    assert "retrieve" in payload["tracker"]["recent_actions"]


def test_v3_repl_set_complexity_and_uncertainty(v3_repl):
    v3_repl.run("set_complexity 0.9")
    v3_repl.run("set_uncertainty 0.7")
    out = v3_repl.run("state")
    import json

    payload = json.loads(out)
    assert abs(payload["tracker"]["complexity_score"] - 0.9) < 0.01
    assert abs(payload["tracker"]["uncertainty_score"] - 0.7) < 0.01


def test_v3_repl_set_complexity_clamps(v3_repl):
    v3_repl.run("set_complexity 5.0")
    out = v3_repl.run("state")
    import json

    payload = json.loads(out)
    # Tracker clamps to [0, 1].
    assert payload["tracker"]["complexity_score"] == 1.0


def test_v3_repl_reset_tracker(v3_repl):
    v3_repl.run("record answer")
    v3_repl.run("record answer")
    v3_repl.run("set_complexity 0.9")
    v3_repl.run("reset_tracker")
    out = v3_repl.run("state")
    import json

    payload = json.loads(out)
    assert payload["tracker"]["consecutive_answer_directly"] == 0
    assert payload["tracker"]["complexity_score"] == 0.0


def test_v3_repl_help_lists_v3_commands(v3_repl):
    out = v3_repl.run("help")
    assert "state" in out
    assert "record <name|answer>" in out
    assert "set_complexity" in out
    assert "set_uncertainty" in out


def test_v2_repl_help_does_not_list_v3_commands(repl):
    out = repl.run("help")
    assert "state" not in out
    assert "record <name|answer>" not in out


def test_v3_repl_decide_with_complexity_fires_explore(v3_repl):
    """End-to-end: set complexity high, decide, get EXPLORE."""
    v3_repl.run("set_complexity 0.9")
    out = v3_repl.run("decide --input 'what changed in fastapi 0.118'")
    assert "explore" in out


def test_v3_repl_set_complexity_requires_value(v3_repl):
    out = v3_repl.run("set_complexity")
    assert "usage" in out


def test_v3_repl_set_complexity_rejects_non_number(v3_repl):
    out = v3_repl.run("set_complexity banana")
    assert "requires a number" in out
