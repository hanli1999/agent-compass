import json
import subprocess
import sys

from agent_compass import Compass, CompassConfig
from agent_compass.models import DecisionAction, DecisionContext, TaskStatus


def test_retrieval_gate_for_missing_context(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    result = compass.decide(DecisionContext(user_input="latest version", time_sensitive=True))
    assert result.action is DecisionAction.RETRIEVE
    assert "time_sensitive" in result.reason_codes


def test_approval_gate_wins_over_other_decisions(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    result = compass.decide(DecisionContext(user_input="publish", external_side_effect=True, task_in_progress=True))
    assert result.action is DecisionAction.PAUSE_FOR_APPROVAL
    assert result.requires_user


def test_small_ordinary_request_can_answer_directly(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    result = compass.decide(DecisionContext(user_input="format this text", has_sufficient_context=True))
    assert result.action is DecisionAction.ANSWER_DIRECTLY


def test_cli_doctor_and_decide(tmp_path):
    env = {"AGENT_COMPASS_DATA_DIR": str(tmp_path), "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[2] / "src")}
    doctor = subprocess.run(
        [sys.executable, "-m", "agent_compass.cli", "--format", "json", "doctor"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, **env},
    )
    assert doctor.returncode == 0
    assert json.loads(doctor.stdout)["ok"] is True


def test_task_persists(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    task = compass.tasks.create("run tests")
    assert compass.tasks.get(task.task_id)["goal"] == "run tests"
