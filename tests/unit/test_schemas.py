"""Schema validation tests.

These tests exercise the bundled JSON schemas against the structures the
library actually emits. They require the ``jsonschema`` package, which is a
development-time optional dependency (see ``pyproject.toml``).
"""
import json
from pathlib import Path

import pytest

from agent_compass import Compass, CompassConfig
from agent_compass.models import DecisionContext
from agent_compass.schemas import validate


def _sample_decision(tmp_path):
    return Compass(CompassConfig(data_dir=tmp_path)).decide(
        DecisionContext(user_input="latest version", time_sensitive=True)
    ).to_dict()


def test_decision_schema_accepts_library_output(tmp_path):
    document = _sample_decision(tmp_path)
    ok, errors = validate("decision", document)
    assert ok, errors


def test_task_schema_accepts_library_output(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    task = compass.tasks.create("schema check").to_dict()
    ok, errors = validate("task", task)
    assert ok, errors


def test_memory_schema_accepts_library_output(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    memory = compass.memory.propose("schema check", memory_type="task_lesson").to_dict()
    ok, errors = validate("memory", memory)
    assert ok, errors


def test_feedback_schema_accepts_library_output(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    event = compass.feedback.record("ok", task_id="t1").to_dict()
    ok, errors = validate("feedback", event)
    assert ok, errors


def test_unknown_schema_returns_error():
    ok, errors = validate("not-a-schema", {})
    assert ok is False
    assert errors


def test_decision_schema_rejects_bad_action(tmp_path):
    document = _sample_decision(tmp_path)
    document["action"] = "explode"
    ok, errors = validate("decision", document)
    assert ok is False
    assert errors
