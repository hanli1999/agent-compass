# Agent Compass

**Local-first, provider-neutral behavior and task state for LLM agents.**

Agent Compass helps an agent decide when to retrieve information, when to ask a clarifying question, when to pause for approval, how to resume interrupted work, and how to retain only privacy-approved task knowledge.

> It is a state and policy layer, not a model, an autonomous life form, or a replacement for human approval.

## 30-second start

```bash
python -m pip install -e .
agent-compass doctor
agent-compass decide --input "What is the latest Python version?" --time-sensitive
agent-compass task create "Run tests and write a report"
```

The core runs offline and does not require an API key or a specific LLM.

## What it does

```text
user request
   ↓
local context check
   ↓
retrieval / clarification / approval gate
   ↓
task checkpoint and resumable state
   ↓
feedback and privacy-reviewed memory
```

### Policy decisions

The deterministic policy engine returns an action, reason codes, confidence, scope, and policy version. It does not execute tools. A caller may use an LLM for classification, but the final safety gates remain deterministic.

### Resumable tasks

Tasks have explicit states, checkpoints, recovery notes, and idempotency support. A process restart should resume from a recorded checkpoint instead of replaying an entire conversation or repeating an external side effect.

### Privacy boundary

Four levels are available: `public`, `local_only`, `sensitive`, and `secret`. Secrets are blocked from remote transfer and from memory proposals. Sensitive text is redacted before remote use. The project never stores credentials.

### Memory lifecycle

Memory is a proposal, not an automatic transcript dump. Candidates are scored with the versioned `activation-v1` formula, inspected for secrets, and then accepted, rejected, archived, or deleted. The default is local-only storage.

## CLI examples

```bash
# Inspect a request
agent-compass decide --input "查一下最新版本" --time-sensitive

# Create and inspect a resumable task
agent-compass task create "完成项目测试"
agent-compass task show task_...

# Scan before sharing text
agent-compass privacy scan --input notes.txt

# Reproduce a memory score
agent-compass memory score --access-count 3 --days 2 --keywords 2 --type task_lesson
```

## Claude Code and other agents

The repository includes examples for SessionStart/Stop and JSONL integration. The core does not depend on Claude Code, Anthropic, OpenAI, LangChain, or a cloud database. Use the JSONL protocol or Python API from any agent runtime.

## Non-goals and honest limits

Agent Compass provides persistent context, task continuity, feedback events, and auditable policy decisions. It does **not** provide consciousness, subjective experience, true autonomous life, guaranteed permanent memory, online model training, or permission to replace human approval for high-impact actions.

## Privacy before publishing

Never commit real conversations, private memory, API keys, user paths, account IDs, tokens, or session cookies. Use `config/*.example.yaml`, synthetic fixtures, and a local data directory outside the repository.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest
```

See `docs/architecture.md`, `docs/behavior-policy.md`, and `docs/privacy-boundary.md` for design details.

## License

MIT
