# CLI Integration

The JSONL protocol is intentionally small. Every line is a JSON object with `type`, `request_id`, and `payload`. Responses mirror the shape and include the same `request_id` so the host can correlate them.

## Request / response

```json
{"type":"decision.request","request_id":"req_1","payload":{"user_input":"latest version","time_sensitive":true}}
{"type":"decision.response","request_id":"req_1","payload":{"action":"retrieve","reason_codes":["time_sensitive","context_insufficient"],"scope":"local","policy_version":"policy-v2"}}
```

Errors look like:

```json
{"type":"error","request_id":"req_1","payload":{"code":"invalid_request","message":"task.advance requires 'task_id'"}}
```

## Supported request types

| `type` | Required payload fields | Notes |
| --- | --- | --- |
| `decision.request` | `user_input` | maps to `DecisionContext` |
| `task.create` | `goal` | optional `metadata` keys |
| `task.show` | `task_id` | returns the persisted task or `not_found` |
| `task.list` | — | returns `{tasks: [...]}` newest-first |
| `task.advance` | `task_id` | optional `target`, `completed_step`, `reason` |
| `task.checkpoint` | `task_id`, `phase` | optional `completed_steps`, `pending_steps`, `notes`, `artifacts` |
| `task.resume` | `task_id` | falls back to the latest persisted checkpoint |
| `memory.propose` | `content` | optional `memory_type`, `privacy`, `keywords`, `importance`, `novelty`, `source`, `related_task_id` |
| `memory.list` | — | optional `status`, `privacy`, `limit` |
| `memory.touch` | `memory_id` | increments `access_count`, recomputes score |
| `memory.archive` | `memory_id` | status becomes `archived` |
| `memory.delete` | `memory_id` | hard delete |
| `memory.prune` | — | optional `below`, `stale_below`, `dry_run` |
| `privacy.scan` | `text` | returns level, matches, blocked, redacted text |
| `feedback.record` | `signal` | optional `label`, `scope`, `task_id`, `decision_id`, `notes` |
| `feedback.list` | — | optional `task_id`, `limit` |
| `idempotency.commit` | `key` | optional `scope`, `task_id` |
| `doctor` | — | reports version, policy version, data_dir, schema_version |

## Why a host process

A host process owns tool execution. Agent Compass only supplies policy, task state, privacy checks, and memory lifecycle operations. A typical loop:

```python
import json, subprocess
for line in sys.stdin:
    decision = json.loads(line)
    response = json.loads(subprocess.check_output(
        ["agent-compass", "decide", "--input", decision["user_input"]],
        text=True,
    ))
    if response["action"] == "retrieve":
        # call the host's retrieval layer
        ...
    elif response["action"] == "pause_for_approval":
        # request human approval
        ...
```
