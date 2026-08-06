# Claude Code Integration

The `hooks/` directory contains examples only. Adapt them to the user's own settings and preserve existing hooks.

Suggested flow:

- SessionStart: call `agent-compass task show` or a project-specific resume command and inject only a short structured summary.
- UserPromptSubmit: call the JSONL decision protocol when a project wants a retrieval/clarification gate.
- PreToolUse: run `agent-compass privacy scan` for high-risk or remote-bound payloads.
- PostToolUse: record a structured success/failure event, not the complete tool output.
- Stop: persist a checkpoint and leave a resumable task ID.

Do not put private memory or credentials in a public skill file.
