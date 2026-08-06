# Claude Code Integration

The `hooks/` directory contains examples only. Adapt them to the user's own settings and preserve existing hooks.

## Suggested hook flow

| Event | Purpose | Example command |
| --- | --- | --- |
| `SessionStart` | Verify the local compass is healthy and report the data directory. | `agent-compass doctor` |
| `UserPromptSubmit` | Get a deterministic policy decision for the user's prompt. | `agent-compass decide --input "$PROMPT" --time-sensitive` |
| `PreToolUse` | Block secret-bearing payloads before they leave the local box. | `agent-compass privacy scan --input "$TOOL_INPUT_PATH"` |
| `PostToolUse` | Record a structured success/failure event, not the complete tool output. | `agent-compass feedback add --signal ok --notes "from hook"` |
| `Stop` | Persist a checkpoint so a future session can resume. | `agent-compass task checkpoint $TASK_ID final --note "session ended"` |

## JSONL integration

For hosts that prefer request/response pairs over CLI one-shots, run the protocol as a long-lived process and pipe JSONL:

```bash
agent-compass serve < requests.jsonl > responses.jsonl
```

Each request must include `type` and `request_id`. The response includes the same `request_id`. See `docs/cli-integration.md` for the full type table.

## Safety reminders

* Do not put private memory, real conversations, API keys, or tokens into a public skill file.
* The privacy detector is a baseline, not a complete DLP product. Domain-specific secrets need domain-specific detectors.
* `agent-compass decide` never executes tools. The host process owns execution.
