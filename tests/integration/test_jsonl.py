import json
import subprocess
import sys


def test_jsonl_decision_protocol(tmp_path):
    payload = {"type": "decision.request", "request_id": "r1", "payload": {"user_input": "latest", "time_sensitive": True}}
    env = {**__import__("os").environ, "AGENT_COMPASS_DATA_DIR": str(tmp_path), "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[2] / "src")}
    result = subprocess.run([sys.executable, "-c", "from agent_compass.cli import jsonl_loop; jsonl_loop()"], input=json.dumps(payload) + "\n", text=True, capture_output=True, env=env)
    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["type"] == "decision.response"
    assert response["payload"]["action"] == "retrieve"
