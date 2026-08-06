"""Formatter tests: both JSON and text output."""
import json
import os

import pytest

from agent_compass.formatters import JsonFormatter, TextFormatter, make_formatter


@pytest.fixture(autouse=True)
def disable_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    yield


def test_json_formatter_emits_pure_json():
    formatter = JsonFormatter()
    decision = {"action": "retrieve", "reason_codes": ["time_sensitive"], "scope": "local", "policy_version": "policy-v2"}
    rendered = formatter.render_decision(decision)
    assert json.loads(rendered) == decision


def test_text_formatter_includes_human_fields():
    formatter = TextFormatter(color=False)
    out = formatter.render_decision(
        {
            "decision_id": "dec_1",
            "action": "retrieve",
            "reason_codes": ["time_sensitive", "context_insufficient"],
            "confidence": 0.9,
            "scope": "local",
            "policy_version": "policy-v2",
            "requires_user": False,
        }
    )
    assert "decision dec_1" in out
    assert "action:" in out
    assert "time_sensitive" in out


def test_text_formatter_handles_lists_gracefully():
    formatter = TextFormatter(color=False)
    out = formatter.render_tasks([])
    assert "no tasks" in out

    out = formatter.render_memories([])
    assert "no memories" in out


def test_text_formatter_color_is_disabled_by_env():
    formatter = TextFormatter(color=True)
    os.environ["NO_COLOR"] = "1"
    formatter_no_color = TextFormatter(color=True)
    out = formatter.render_doctor({"ok": True, "version": "0.3.0", "policy_version": "policy-v2", "data_dir": "/tmp", "schema_version": "1"})
    out_no_color = formatter_no_color.render_doctor({"ok": True, "version": "0.3.0", "policy_version": "policy-v2", "data_dir": "/tmp", "schema_version": "1"})
    # When NO_COLOR is set the output should not contain raw escape codes.
    assert "\x1b[" not in out_no_color


def test_make_formatter_returns_correct_type():
    assert isinstance(make_formatter("json"), JsonFormatter)
    assert isinstance(make_formatter("text"), TextFormatter)
    with pytest.raises(ValueError):
        make_formatter("yaml")


def test_text_formatter_truncates_long_content():
    formatter = TextFormatter(color=False)
    out = formatter.render_memories(
        [{"memory_id": "m1", "status": "active", "privacy": "local_only", "score": 0.5, "content": "x" * 200}]
    )
    assert "..." in out
