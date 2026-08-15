"""Tests for the v0.8.0 hooks installer.

The installer writes to ``~/.claude/settings.json`` by default. To
keep tests hermetic we pass an explicit ``settings_path`` rooted in
``tmp_path`` for every call.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_compass.runtime import install_claude_code_hooks
from agent_compass.runtime.hooks_install import EVENTS


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_creates_parent_dir(tmp_path):
    target = tmp_path / "nested" / "settings.json"
    report = install_claude_code_hooks(settings_path=target)
    assert target.exists()
    assert report.settings_path == target


def test_install_writes_all_five_events(tmp_path):
    target = tmp_path / "settings.json"
    report = install_claude_code_hooks(settings_path=target)
    data = _read(target)
    hooks_root = data.get("hooks", {})
    for event in EVENTS:
        assert event in hooks_root
    assert set(report.events_installed) == set(EVENTS)


def test_install_is_idempotent(tmp_path):
    target = tmp_path / "settings.json"
    install_claude_code_hooks(settings_path=target)
    report = install_claude_code_hooks(settings_path=target)
    # Second call: every event is already present, so no installs.
    assert report.events_installed == []
    assert set(report.already_present) == set(EVENTS)


def test_install_preserves_existing_hooks(tmp_path):
    target = tmp_path / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup|resume",
                            "hooks": [{"type": "command", "command": "echo custom"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    report = install_claude_code_hooks(settings_path=target)
    data = _read(target)
    session = data["hooks"]["SessionStart"]
    commands = [
        h.get("command")
        for entry in session
        for h in entry.get("hooks", [])
        if h.get("type") == "command"
    ]
    # The custom hook survived.
    assert "echo custom" in commands
    # Our hook was appended.
    assert "agent-compass doctor" in commands
    # The SessionStart event was *partially* present (one of our two
    # commands was missing), so the function added the missing one
    # and reported the event as installed. The four other events
    # were entirely missing and got installed too.
    assert "SessionStart" in report.events_installed


def test_install_overwrite_replaces_events(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup|resume",
                            "hooks": [{"type": "command", "command": "echo custom"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    install_claude_code_hooks(settings_path=target, overwrite=True)
    data = _read(target)
    session = data["hooks"]["SessionStart"]
    commands = [
        h.get("command")
        for entry in session
        for h in entry.get("hooks", [])
        if h.get("type") == "command"
    ]
    assert "echo custom" not in commands
    assert "agent-compass doctor" in commands


def test_install_treats_corrupt_json_as_empty(tmp_path):
    target = tmp_path / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ this is not valid json", encoding="utf-8")
    report = install_claude_code_hooks(settings_path=target)
    # We should still install cleanly.
    data = _read(target)
    assert "hooks" in data
    assert len(report.events_installed) == len(EVENTS)


def test_install_report_is_json_serialisable(tmp_path):
    target = tmp_path / "settings.json"
    report = install_claude_code_hooks(settings_path=target)
    blob = json.dumps(report.to_dict(), ensure_ascii=False)
    parsed = json.loads(blob)
    assert "settings_path" in parsed
    assert "events_installed" in parsed
