# Architecture

```text
agent runtime
    ↓ JSONL / Python API
policy gates ─── privacy boundary
    ↓                 ↓
task state + checkpoints   memory lifecycle
    ↓                 ↓
SQLite local store ← feedback events
    ↓
adapters (optional) ──→ external models
```

The core is provider-neutral. Policy decides; adapters execute. The task store records progress and side effects. Privacy is checked before persistence or remote transfer.

## Design rules

1. A model may suggest a decision, but deterministic gates validate it.
2. A task cannot be considered complete without a recorded completion state.
3. A restart resumes from a checkpoint and does not blindly replay side effects.
4. Memory is proposed, classified, privacy-scanned, scored, and versioned.
5. Remote transfer is opt-in; secrets are always blocked.
6. Idempotency keys survive process restarts so retries do not double-execute side effects.

## Policy versioning

`policy_version` is recorded on every `Decision`. The current value is `policy-v2`. The golden test suite (`tests/golden/`) pins the reason codes, actions, and scopes for representative inputs so the contract does not drift silently.

## Component map

| Module | Role |
| --- | --- |
| `agent_compass.policy.engine` | Deterministic decision engine, versioned reason codes |
| `agent_compass.tasks.service` | Task persistence, transitions, checkpoints, resume |
| `agent_compass.tasks.state_machine` | Allowed state transitions, idempotency registry |
| `agent_compass.memory.service` | Full memory lifecycle, score-based pruning |
| `agent_compass.memory.scoring` | Versioned `activation-v1` formula |
| `agent_compass.privacy.boundary` | Local privacy classifier and conservative redactor |
| `agent_compass.storage.sqlite` | Single-file local store with schema versioning |
| `agent_compass.protocol` | JSONL request/response dispatcher |
| `agent_compass.adapters` | Optional `LLMAdapter` Protocol + offline `NullAdapter` |
| `agent_compass.schemas` | Optional JSON Schema validation for the bundled contracts |
