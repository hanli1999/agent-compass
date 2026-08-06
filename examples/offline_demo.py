"""Offline demo: decide, checkpoint, recover, and privacy-check without an LLM."""
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_compass import Compass, CompassConfig
from agent_compass.models import DecisionContext, TaskStatus
from agent_compass.tasks.state_machine import Checkpoint


with TemporaryDirectory() as directory:
    compass = Compass(CompassConfig(data_dir=Path(directory)))
    decision = compass.decide(DecisionContext(user_input="最新版本是什么？", time_sensitive=True))
    print("decision:", decision.action.value, decision.reason_codes)

    task = compass.tasks.create("完成离线演示")
    compass.tasks.machine.transition(task, TaskStatus.RUNNING)
    compass.tasks.machine.checkpoint(task, Checkpoint(task.task_id, "verify", ["plan"], ["report"], notes=["safe to resume"]))
    print("resume:", compass.tasks.machine.resume(task))
    print("privacy:", compass.privacy.inspect("contact alice@example.com"))
