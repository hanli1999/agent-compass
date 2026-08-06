"""Smoke test the CLI end-to-end via subprocess."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_doctor_reports_version(tmp_path):
    result = _run(["doctor"], tmp_path)
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["version"] == "0.2.0"
    assert body["policy_version"] == "policy-v2"


def test_decide_cli_emits_decision(tmp_path):
    result = _run(["decide", "--input", "latest version", "--time-sensitive"], tmp_path)
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["action"] == "retrieve"


def test_task_create_advance_resume(tmp_path):
    create = _run(["task", "create", "demo"], tmp_path)
    task_id = json.loads(create.stdout)["task_id"]
    advance = _run(["task", "advance", task_id, "--target", "running", "--completed-step", "plan"], tmp_path)
    assert json.loads(advance.stdout)["status"] == "running"
    checkpoint = _run(
        [
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
    resume = _run(["task", "resume", task_id], tmp_path)
    body = json.loads(resume.stdout)
    assert body["resume"] is True
    assert body["next_step"] == "report"


def test_memory_propose_list_archive(tmp_path):
    propose = _run(["memory", "propose", "--content", "rule one", "--type", "task_lesson"], tmp_path)
    memory_id = json.loads(propose.stdout)["memory_id"]
    listed = _run(["memory", "list"], tmp_path)
    body = json.loads(listed.stdout)
    assert any(item["memory_id"] == memory_id for item in body["memories"])
    archive = _run(["memory", "archive", memory_id], tmp_path)
    assert json.loads(archive.stdout)["status"] == "archived"


def test_privacy_scan_via_text_flag(tmp_path):
    result = _run(["privacy", "scan", "--text", "contact alice@example.com"], tmp_path)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["level"] == "sensitive"
    assert "email" in body["matches"]


def test_validate_rejects_invalid_action(tmp_path, tmp_path_factory):
    fixture = tmp_path_factory.mktemp("fixtures") / "bad.json"
    fixture.write_text(json.dumps({"action": "explode", "reason_codes": [], "confidence": 0.5, "policy_version": "policy-v2"}), encoding="utf-8")
    result = _run(["validate", "decision", str(fixture)], tmp_path)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["errors"]
