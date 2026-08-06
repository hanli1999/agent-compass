# CLI Integration

The JSONL protocol is intentionally small:

```json
{"type":"decision.request","request_id":"req_1","payload":{"user_input":"latest version","time_sensitive":true}}
```

A response has the same request ID:

```json
{"type":"decision.response","request_id":"req_1","payload":{"action":"retrieve","reason_codes":["time_sensitive","context_insufficient"]}}
```

A host process owns tool execution. Agent Compass only supplies policy, task state, privacy checks, and memory lifecycle operations.
