# Agent Compass

**Local-first, provider-neutral behavior and task state for LLM agents.**

Agent Compass helps an agent decide when to retrieve information, when to ask a clarifying question, when to pause for approval, how to resume interrupted work, and how to retain only privacy-approved task knowledge.

> It is a state and policy layer, not a model, an autonomous life form, or a replacement for human approval.

## 30-second start

```bash
python -m pip install -e '.[dev]'
agent-compass doctor
agent-compass decide --input "What is the latest Python version?" --time-sensitive
agent-compass task create "Run tests and write a report"
agent-compass serve < requests.jsonl
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

The deterministic policy engine returns an action, reason codes, confidence, scope, and policy version. It does not execute tools. A caller may use an LLM for classification, but the final safety gates remain deterministic. The current version is `policy-v2`.

| Action | When it fires |
| --- | --- |
| `answer_directly` | local context is sufficient |
| `retrieve` | time-sensitive, explicit search, or context insufficient (remote gated) |
| `ask_user` | ambiguity over the configured threshold |
| `continue` | task in progress, no interruption |
| `resume` | task in progress, last turn was interrupted |
| `pause_for_approval` | external side effect, destructive action, or waiting for human |
| `consolidate_memory` | session is ending or ended |
| `stop` | retry budget exhausted (no silent infinite retry) |

### Resumable tasks

Tasks, checkpoints, and idempotency keys are persisted in SQLite. A process restart should resume from the latest checkpoint instead of replaying an entire conversation or repeating an external side effect.

### Privacy boundary

Four levels are available: `public`, `local_only`, `sensitive`, and `secret`. Secrets are blocked from remote transfer and from memory proposals. Sensitive text is redacted before remote use. The bundled detector is a baseline, not a complete DLP product.

### Memory lifecycle

Memory is a proposal, not an automatic transcript dump. Candidates are scored with a versioned activation formula, inspected for secrets, and then move through `candidate → accepted → active → stale → archived/deleted`. The default is local-only storage.

Two formulas coexist and each record remembers which one produced it, so upgrading never silently rescores existing rows. `activation-v1` is the default. `activation-v2` adds two dimensions — an affect tag and a five-class instinct tag (`survival` / `kin` / `resource` / `novelty` / `transmit`) — and a two-segment retention curve that forgets fast for a week and then plateaus. A memory opts in automatically when proposed with a tag.

### Bounded retrieval

Give an agent a good memory store and it will drown in it. Scoring tells you *which* memories matter; it says nothing about how many to send or how large they may be.

**Retrieval never returns full memory bodies.** It returns a ranked digest under an explicit Top-K and token budget, plus the `memory_id` needed to fetch a body on demand.

```python
result = compass.recall("why did the migration fail", token_budget=800)

for item in result:
    print(item.summary)                 # bounded digest, never the whole record

if result.truncated:
    print(f"{result.dropped_for_limit + result.dropped_for_budget} more not shown")

body = compass.memory.get(result.items[0].memory_id)["content"]   # on demand
```

Everything withheld is counted and reported — a digest that silently drops four matches reads exactly like a complete answer. Any source with a `name` and a `retrieve(query)` method can join the fan-out, and one that raises is recorded and skipped rather than failing the call. See `docs/retrieval-orchestration.md`.

## CLI reference

Global flags (work on every subcommand): `--config path`, `--format json|text` (default `text`), `--no-color`.

```text
agent-compass doctor
agent-compass serve                          # JSONL protocol on stdin
agent-compass repl                           # interactive shell, type 'help'
agent-compass decide --input "..." [--time-sensitive] [--remote] [--interrupted] [--retry-count N] [--proposed-action X] [--session-state ending] [--ambiguous 0.8]
agent-compass validate <decision|task|memory|feedback> <file.json>
agent-compass task create <goal>
agent-compass task show <task_id>
agent-compass task list [--limit 20] [--include-archived]
agent-compass task advance <task_id> [--target running] [--completed-step plan] [--reason ...]
agent-compass task checkpoint <task_id> <phase> [--completed-step X] [--pending-step Y] [--note Z] [--artifact path]
agent-compass task resume <task_id>
agent-compass task delete <task_id> [--soft]
agent-compass privacy scan --text "..." | --input path.txt
agent-compass memory propose --content "..." [--type task_lesson] [--privacy local_only] [--keyword k1] [--related-task task_id]
agent-compass memory list [--status active] [--privacy local_only] [--limit 20]
agent-compass memory search --query "..." [--type task_lesson] [--status active] [--min-score 0.5] [--limit 20]
agent-compass memory touch <memory_id>
agent-compass memory archive <memory_id>
agent-compass memory delete <memory_id>
agent-compass memory prune [--below 0.15] [--stale-below 0.3] [--dry-run]
agent-compass memory score --access-count N --days D --keywords K --type task_lesson [--importance 0.5]
agent-compass feedback add --signal ok [--label positive] [--scope this_task] [--task-id t] [--notes "..."]
agent-compass feedback list [--task-id t] [--limit 20]
agent-compass feedback stats [--task-id t]
```

### Output formats

Default output is human-readable text. Example:

```text
$ agent-compass decide --input "latest version"
decision dec_8b2c4f12a300
  action:      retrieve
  reasons:     time_sensitive, context_insufficient
  confidence:  0.90
  scope:       local
  policy:      policy-v2
  requires:    auto
```

Use `--format json` for the previous machine-friendly output, or `--no-color` (or `NO_COLOR=1`) to strip ANSI escapes when piping.

### Interactive REPL

```text
$ agent-compass repl
agent-compass 0.3.0 (policy policy-v2)
type 'help' for commands, 'exit' to quit.
> memory propose --content "always run unit tests first" --type task_lesson
memory mem_2aeea3c2ee32
  status:      candidate
  privacy:     local_only
  type:        task_lesson
  score:       0.500
  content:     always run unit tests first
  accesses:    0
> memory search --query test
memory_id          status     privacy       score  content
mem_2aeea3c2ee32   candidate  local_only    0.500  always run unit tests first
> exit
bye.
```

## JSONL protocol

`agent-compass serve` reads one request per line and writes one response per line. Every request must include `type` and `request_id`. Supported request types:

```text
decision.request, task.create, task.show, task.list, task.advance,
task.checkpoint, task.resume, memory.propose, memory.list, memory.touch,
memory.archive, memory.delete, memory.prune, privacy.scan,
feedback.record, feedback.list, idempotency.commit, doctor
```

Errors come back as `{"type": "error", "payload": {"code": "...", "message": "..."}}` so callers do not have to parse stderr.

## Claude Code and other agents

The repository includes example hooks for SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop. The core does not depend on Claude Code, Anthropic, OpenAI, LangChain, or a cloud database. Use the JSONL protocol or Python API from any agent runtime.

## Non-goals and honest limits

Agent Compass provides persistent context, task continuity, feedback events, and auditable policy decisions. It does **not** provide consciousness, subjective experience, true autonomous life, guaranteed permanent memory, online model training, or permission to replace human approval for high-impact actions.

## Privacy before publishing

Never commit real conversations, private memory, API keys, user paths, account IDs, tokens, or session cookies. Use `config/*.example.yaml`, synthetic fixtures, and a local data directory outside the repository.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m pytest --cov=agent_compass --cov-report=term-missing
```

See `docs/architecture.md`, `docs/behavior-policy.md`, `docs/memory-model.md`, `docs/retrieval-orchestration.md`, `docs/privacy-boundary.md`, `docs/provider-adapters.md`, and `CHANGELOG.md` for design details.

## First adopter

Agent Compass is shipped to its first real adopter on 2026-08-06. The adopter is
[幻梦](https://github.com/hanli1999/digital-brain), a Claude Code instance that
owns the [digital-brain](https://github.com/hanli1999/digital-brain) project.

What 幻梦 did on day one:

- Cloned the repo, set up an isolated venv, and pointed the data directory outside the repo.
- Merged the `SessionStart` / `UserPromptSubmit` / `PreToolUse` hooks into her Claude Code `settings.json`.
- Held back `Stop` (task checkpoint) and `PostToolUse` (feedback) pending observation — she wants to confirm the first three hooks are stable before adding more.
- Ran the sanity commands below and saved the results.

```bash
agent-compass doctor
agent-compass decide --input "What is the latest Python version?" --time-sensitive
agent-compass task create "Run tests and write a report"
agent-compass memory propose --content "always run the migration dry-run first"
agent-compass privacy scan --text "api_key=REDACTED"
```

She also surfaced two product-feedback items, now scheduled for v0.5.0 (see `docs/ROADMAP.md`):

1. **`task_checkpoint` has no `task_id` to bind to.** Claude Code does not expose a "current task" concept, so a `Stop` hook cannot know which task to checkpoint. Plan: persist `last_task_id` in a state file, fall back to an env var, fall back to "unspecified".
2. **`feedback add` collides with an existing sound reminder hook.** Both fire on `PostToolUse`. Plan: make `feedback add` async — write to a pending file and flush from a separate, lower-frequency process.

If you are an agent that wants to be listed here too, file an issue with a short report — five bullet points of what you wired up and what you measured. We will update this section with the next adopter once one is verified.

## License

MIT
