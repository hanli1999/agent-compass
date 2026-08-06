"""Golden tests: pin the deterministic outputs of the policy engine and CLI.

The goal is to catch silent regressions in reason codes, scopes, and confidence
values. If you intentionally change policy semantics, update the snapshot
files in ``tests/golden/snapshots/`` alongside the change.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = Path(__file__).parent / "snapshots"


def _decide_cli(payload: dict, env: dict) -> dict:
    args = [sys.executable, "-m", "agent_compass.cli", "--format", "json", "decide", "--input", payload["user_input"]]
    if payload.get("time_sensitive"):
        args.append("--time-sensitive")
    if payload.get("remote"):
        args.append("--remote")
    if payload.get("interrupted"):
        args.append("--interrupted")
    if payload.get("retry_count"):
        args.extend(["--retry-count", str(payload["retry_count"])])
    if payload.get("session_state"):
        args.extend(["--session-state", payload["session_state"]])
    if payload.get("proposed_action"):
        for action in payload["proposed_action"]:
            args.extend(["--proposed-action", action])
    if "ambiguity" in payload:
        args.extend(["--ambiguous", str(payload["ambiguity"])])
    result = subprocess.run(args, capture_output=True, text=True, env=env, check=True)
    return json.loads(result.stdout)


def test_decision_golden_snapshots(tmp_path):
    env = {
        **__import__("os").environ,
        "AGENT_COMPASS_DATA_DIR": str(tmp_path),
        "PYTHONPATH": str(ROOT / "src"),
    }
    fixtures = json.loads((Path(__file__).parent / "decisions.json").read_text(encoding="utf-8"))
    for fixture in fixtures:
        actual = _decide_cli(fixture["input"], env)
        snapshot = fixture["expected"]
        for key, value in snapshot.items():
            assert actual.get(key) == value, f"mismatch in {fixture['name']} for {key}: {actual!r}"
