import pytest

from agent_compass.models import TaskStatus
from agent_compass.tasks.state_machine import Checkpoint, IdempotencyRegistry, TaskStateMachine
from agent_compass.models import Task


def test_task_checkpoint_and_resume():
    machine = TaskStateMachine()
    task = Task("demo")
    machine.transition(task, TaskStatus.RUNNING)
    machine.checkpoint(task, Checkpoint(task.task_id, "research", ["plan"], ["verify"], artifacts=["notes.md"]))
    result = machine.resume(task)
    assert result["resume"] is True
    assert result["next_step"] == "verify"


def test_approval_state_requires_user():
    machine = TaskStateMachine()
    task = Task("demo")
    machine.transition(task, TaskStatus.RUNNING)
    machine.transition(task, TaskStatus.WAITING_FOR_APPROVAL)
    result = machine.resume(task)
    assert result["requires_user"] is True


def test_invalid_completed_transition():
    machine = TaskStateMachine()
    task = Task("demo")
    machine.transition(task, TaskStatus.RUNNING)
    machine.transition(task, TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        machine.transition(task, TaskStatus.RUNNING)


def test_idempotency_registry():
    registry = IdempotencyRegistry()
    assert registry.can_execute("send:1")
    registry.commit("send:1")
    assert not registry.can_execute("send:1")
    with pytest.raises(ValueError):
        registry.commit("send:1")
