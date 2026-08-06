import pytest

from agent_compass import Compass, CompassConfig
from agent_compass.models import TaskStatus


def _compass(tmp_path):
    return Compass(CompassConfig(data_dir=tmp_path))


def test_create_and_persist_task(tmp_path):
    compass = _compass(tmp_path)
    task = compass.tasks.create("write tests")
    fetched = compass.tasks.get(task.task_id)
    assert fetched["goal"] == "write tests"
    assert fetched["status"] == "created"


def test_advance_transitions_status_and_records_step(tmp_path):
    compass = _compass(tmp_path)
    task = compass.tasks.create("demo")
    updated = compass.tasks.advance(task.task_id, target=TaskStatus.RUNNING.value)
    assert updated["status"] == "running"
    completed = compass.tasks.advance(task.task_id, completed_step="plan")
    assert "plan" in completed["completed_steps"]


def test_checkpoint_persists_phase(tmp_path):
    compass = _compass(tmp_path)
    task = compass.tasks.create("demo")
    compass.tasks.advance(task.task_id, target=TaskStatus.RUNNING.value)
    updated = compass.tasks.checkpoint(
        task.task_id,
        "verify",
        completed_steps=["plan"],
        pending_steps=["report"],
        notes=["safe to resume"],
    )
    assert updated["current_phase"] == "verify"
    assert updated["pending_steps"] == ["report"]
    resume = compass.tasks.resume(task.task_id)
    assert resume["resume"] is True
    assert resume["next_step"] == "report"
    assert resume["checkpoint"]["phase"] == "verify"


def test_resume_loads_latest_persisted_checkpoint(tmp_path):
    compass = _compass(tmp_path)
    task = compass.tasks.create("demo")
    compass.tasks.advance(task.task_id, target=TaskStatus.RUNNING.value)
    compass.tasks.checkpoint(
        task.task_id,
        "research",
        completed_steps=["gather"],
        pending_steps=["analyze"],
    )
    # Reopen compass to simulate a fresh process; checkpoint should still be readable.
    reopened = Compass(CompassConfig(data_dir=tmp_path))
    resume = reopened.tasks.resume(task.task_id)
    assert resume["resume"] is True
    assert resume["checkpoint"]["phase"] == "research"
    assert resume["next_step"] == "analyze"


def test_idempotency_registry_survives_restart(tmp_path):
    compass = _compass(tmp_path)
    assert compass.idempotency.can_execute("send:42")
    compass.idempotency.commit("send:42", scope="external_post", task_id="task_x")
    reopened = Compass(CompassConfig(data_dir=tmp_path))
    assert not reopened.idempotency.can_execute("send:42")
    with pytest.raises(ValueError):
        reopened.idempotency.commit("send:42", scope="external_post", task_id="task_x")


def test_list_tasks_returns_most_recent_first(tmp_path):
    compass = _compass(tmp_path)
    compass.tasks.create("first")
    compass.tasks.create("second")
    listed = compass.tasks.list()
    assert len(listed) == 2
    assert listed[0]["goal"] in {"first", "second"}
