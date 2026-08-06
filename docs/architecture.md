# Architecture

```text
agent runtime
    ↓ JSONL / Python API
policy gates ─── privacy boundary
    ↓                 ↓
task state + checkpoints   memory proposals
    ↓                 ↓
SQLite local store ← feedback events
```

The core is provider-neutral. Policy decides; adapters execute. The task store records progress and side effects. Privacy is checked before persistence or remote transfer.

## Design rules

1. A model may suggest a decision, but deterministic gates validate it.
2. A task cannot be considered complete without a recorded completion state.
3. A restart resumes from a checkpoint and does not blindly replay side effects.
4. Memory is proposed, classified, privacy-scanned, and versioned.
5. Remote transfer is opt-in; secrets are always blocked.
