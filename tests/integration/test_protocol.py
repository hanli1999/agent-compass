"""End-to-end coverage of the JSONL protocol dispatcher."""
import io
import json

from agent_compass import Compass, CompassConfig
from agent_compass.protocol import handle


def _compass(tmp_path):
    return Compass(CompassConfig(data_dir=tmp_path))


def test_decision_round_trip(tmp_path):
    compass = _compass(tmp_path)
    response = handle(
        compass,
        {
            "type": "decision.request",
            "request_id": "r1",
            "payload": {"user_input": "latest version", "time_sensitive": True},
        },
    )
    assert response["type"] == "decision.response"
    assert response["request_id"] == "r1"
    assert response["payload"]["action"] == "retrieve"


def test_full_task_lifecycle_via_protocol(tmp_path):
    compass = _compass(tmp_path)
    created = handle(compass, {"type": "task.create", "request_id": "c1", "payload": {"goal": "demo"}})
    assert created["type"] == "task.create.response"
    task_id = created["payload"]["task_id"]

    advanced = handle(
        compass,
        {
            "type": "task.advance",
            "request_id": "a1",
            "payload": {"task_id": task_id, "target": "running", "completed_step": "plan"},
        },
    )
    assert advanced["payload"]["status"] == "running"

    checkpointed = handle(
        compass,
        {
            "type": "task.checkpoint",
            "request_id": "k1",
            "payload": {
                "task_id": task_id,
                "phase": "verify",
                "completed_steps": ["plan"],
                "pending_steps": ["report"],
            },
        },
    )
    assert checkpointed["payload"]["current_phase"] == "verify"

    resumed = handle(
        compass,
        {"type": "task.resume", "request_id": "r1", "payload": {"task_id": task_id}},
    )
    assert resumed["payload"]["resume"] is True
    assert resumed["payload"]["next_step"] == "report"


def test_memory_lifecycle_via_protocol(tmp_path):
    compass = _compass(tmp_path)
    proposed = handle(
        compass,
        {
            "type": "memory.propose",
            "request_id": "m1",
            "payload": {"content": "rule one", "memory_type": "task_lesson"},
        },
    )
    assert proposed["type"] == "memory.propose.response"
    memory_id = proposed["payload"]["memory_id"]
    assert proposed["payload"]["status"] == "candidate"

    listed = handle(compass, {"type": "memory.list", "request_id": "l1", "payload": {}})
    assert any(item["memory_id"] == memory_id for item in listed["payload"]["memories"])

    archived = handle(
        compass,
        {"type": "memory.archive", "request_id": "ar1", "payload": {"memory_id": memory_id}},
    )
    assert archived["payload"]["status"] == "archived"


def test_feedback_and_privacy_scan(tmp_path):
    compass = _compass(tmp_path)
    fb = handle(
        compass,
        {
            "type": "feedback.record",
            "request_id": "f1",
            "payload": {"signal": "ok", "task_id": "task_x", "label": "positive"},
        },
    )
    assert fb["payload"]["label"] == "positive"

    scan = handle(
        compass,
        {
            "type": "privacy.scan",
            "request_id": "p1",
            "payload": {"text": "contact alice@example.com"},
        },
    )
    assert scan["payload"]["level"] == "sensitive"
    assert "[REDACTED:email]" in scan["payload"]["redacted"]


def test_idempotency_commit_survives_restart(tmp_path):
    compass = _compass(tmp_path)
    committed = handle(
        compass,
        {
            "type": "idempotency.commit",
            "request_id": "i1",
            "payload": {"key": "send:99", "scope": "external_post", "task_id": "task_x"},
        },
    )
    assert committed["payload"]["committed"] is True

    reopened = _compass(tmp_path)
    with_again = handle(
        reopened,
        {
            "type": "idempotency.commit",
            "request_id": "i2",
            "payload": {"key": "send:99"},
        },
    )
    assert with_again["type"] == "error"
    assert with_again["payload"]["code"] == "invalid_request"


def test_unknown_request_returns_supported_list(tmp_path):
    compass = _compass(tmp_path)
    response = handle(compass, {"type": "nope", "request_id": "u1", "payload": {}})
    assert response["type"] == "error"
    assert "decision.request" in response["payload"]["supported"]


def test_serve_processes_jsonl_lines(tmp_path, monkeypatch):
    from agent_compass.protocol import serve

    monkeypatch.setenv("AGENT_COMPASS_DATA_DIR", str(tmp_path))
    stdin = io.StringIO(
        json.dumps({"type": "doctor", "request_id": "d1", "payload": {}}) + "\n"
    )
    stdout = io.StringIO()
    rc = serve(stdin=stdin, stdout=stdout)
    assert rc == 0
    response = json.loads(stdout.getvalue().strip())
    assert response["type"] == "doctor.response"
    assert response["payload"]["ok"] is True
