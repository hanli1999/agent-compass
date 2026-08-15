"""Smoke test the CLI end-to-end via subprocess."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_compass import __version__

ROOT = Path(__file__).resolve().parents[2]


def _run(args, tmp_path, **kwargs):
    env = {**os.environ, "AGENT_COMPASS_DATA_DIR": str(tmp_path), "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "agent_compass.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        **kwargs,
    )


def test_doctor_reports_version_json(tmp_path):
    result = _run(["--format", "json", "doctor"], tmp_path)
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["version"] == __version__
    assert body["policy_version"] == "policy-v2"


def test_doctor_text_output_is_human_readable(tmp_path):
    result = _run(["--format", "text", "doctor"], tmp_path)
    assert result.returncode == 0
    assert "agent-compass" in result.stdout
    assert "version:" in result.stdout


def test_decide_cli_emits_decision_json(tmp_path):
    result = _run(["--format", "json", "decide", "--input", "latest version", "--time-sensitive"], tmp_path)
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["action"] == "retrieve"


def test_task_create_advance_resume(tmp_path):
    create = _run(["--format", "json", "task", "create", "demo"], tmp_path)
    task_id = json.loads(create.stdout)["task_id"]
    advance = _run(
        ["--format", "json", "task", "advance", task_id, "--target", "running", "--completed-step", "plan"],
        tmp_path,
    )
    assert json.loads(advance.stdout)["status"] == "running"
    checkpoint = _run(
        [
            "--format",
            "json",
            "task",
            "checkpoint",
            task_id,
            "verify",
            "--completed-step",
            "plan",
            "--pending-step",
            "report",
        ],
        tmp_path,
    )
    assert json.loads(checkpoint.stdout)["current_phase"] == "verify"
    resume = _run(["--format", "json", "task", "resume", task_id], tmp_path)
    body = json.loads(resume.stdout)
    assert body["resume"] is True
    assert body["next_step"] == "report"


def test_task_delete_soft_and_hard(tmp_path):
    create = _run(["--format", "json", "task", "create", "to delete"], tmp_path)
    task_id = json.loads(create.stdout)["task_id"]
    soft = _run(["--format", "json", "task", "delete", task_id, "--soft"], tmp_path)
    soft_body = json.loads(soft.stdout)
    assert soft_body["soft"] is True
    assert soft_body["status"] == "archived"
    hard = _run(["--format", "json", "task", "delete", task_id], tmp_path)
    assert json.loads(hard.stdout)["deleted"] is True


def test_memory_propose_list_archive(tmp_path):
    propose = _run(["--format", "json", "memory", "propose", "--content", "rule one", "--type", "task_lesson"], tmp_path)
    memory_id = json.loads(propose.stdout)["memory_id"]
    listed = _run(["--format", "json", "memory", "list"], tmp_path)
    body = json.loads(listed.stdout)
    assert any(item["memory_id"] == memory_id for item in body["memories"])
    archive = _run(["--format", "json", "memory", "archive", memory_id], tmp_path)
    assert json.loads(archive.stdout)["status"] == "archived"


def test_memory_search_filters(tmp_path):
    _run(["--format", "json", "memory", "propose", "--content", "always run unit tests", "--keyword", "test"], tmp_path)
    _run(["--format", "json", "memory", "propose", "--content", "prefer offline workflows", "--keyword", "ci"], tmp_path)
    result = _run(["--format", "json", "memory", "search", "--query", "test"], tmp_path)
    items = json.loads(result.stdout)["memories"]
    assert len(items) == 1
    assert "test" in items[0]["content"].lower()


def test_feedback_stats(tmp_path):
    # v0.5.0+ — feedback add is async by default so it does not block the
    # PostToolUse hook. This test is about the stats aggregation, not
    # the async path, so it uses --sync to skip the pending file.
    _run(["--format", "json", "feedback", "add", "--sync", "--signal", "ok", "--label", "positive", "--task-id", "t1"], tmp_path)
    _run(["--format", "json", "feedback", "add", "--sync", "--signal", "bad", "--label", "negative", "--task-id", "t1"], tmp_path)
    result = _run(["--format", "json", "feedback", "stats", "--task-id", "t1"], tmp_path)
    body = json.loads(result.stdout)
    assert body["total"] == 2
    assert body["by_label"]["positive"] == 1
    assert body["by_label"]["negative"] == 1


def test_privacy_scan_via_text_flag(tmp_path):
    result = _run(["--format", "json", "privacy", "scan", "--text", "contact alice@example.com"], tmp_path)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["level"] == "sensitive"
    assert "email" in body["matches"]


def test_validate_rejects_invalid_action(tmp_path, tmp_path_factory):
    fixture = tmp_path_factory.mktemp("fixtures") / "bad.json"
    fixture.write_text(
        json.dumps({"action": "explode", "reason_codes": [], "confidence": 0.5, "policy_version": "policy-v2"}),
        encoding="utf-8",
    )
    result = _run(["validate", "decision", str(fixture)], tmp_path)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["errors"]


def test_repl_processes_commands(tmp_path):
    """Feed a multi-line script into the REPL and check the output."""
    env = {**os.environ, "AGENT_COMPASS_DATA_DIR": str(tmp_path), "PYTHONPATH": str(ROOT / "src")}
    script = "\n".join(
        [
            "doctor",
            "memory propose --content 'always test first' --type task_lesson",
            "memory list",
            "task create demo repl",
            "task list",
            "feedback add --signal ok --label positive",
            "feedback stats",
            "exit",
            "",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-m", "agent_compass.cli", "repl", "--no-color"],
        input=script,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    assert "agent-compass" in result.stdout
    assert "always test first" in result.stdout
    assert "by_label" in result.stdout


def test_tracker_flush_then_restore_via_cli(tmp_path):
    """v0.9.4+: ``agent-compass tracker flush`` and
    ``agent-compass tracker restore`` round-trip via the CLI so the
    SessionStart / Stop hooks do not need a Python API."""
    # flush creates the file
    flush_result = _run(["tracker", "flush", "--path", str(tmp_path / "tracker.json")], tmp_path)
    assert flush_result.returncode == 0
    payload = json.loads(flush_result.stdout)
    assert payload["flushed"] is True
    assert (tmp_path / "tracker.json").exists()

    # restore without prior flush-state == exit 1 (no state to load)
    no_state = _run(
        ["tracker", "restore", "--path", str(tmp_path / "no-such-file.json")],
        tmp_path,
    )
    assert no_state.returncode == 1

    # restore with the file we just wrote == exit 0
    restore_result = _run(
        ["tracker", "restore", "--path", str(tmp_path / "tracker.json")],
        tmp_path,
    )
    assert restore_result.returncode == 0
    payload = json.loads(restore_result.stdout)
    assert payload["restored"] is True
    assert payload["state"]["schema_version"] == 1
