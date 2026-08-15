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

### Host-side helper (0.8.0+)

A fresh host that wants v3 / web / hooks without reading three CHANGELOG entries can use the `agent_compass.runtime` package:

```python
from agent_compass import Compass
from agent_compass.runtime import build_smart_default_config, apply_smart_defaults, HostLoop

compass = Compass(build_smart_default_config(remote_allowed=True))
apply_smart_defaults(compass)                # v3 on, web adapter wired

loop = HostLoop(compass)
loop.record("retrieve")                      # after a tool call
decision = loop.decide("what next")          # auto-folds the tracker snapshot
loop.record("answer")                        # after a text-only response
```

`install_claude_code_hooks()` writes the five Claude Code events to `~/.claude/settings.json` for hosts that want a working hook set without a manual merge. See `CHANGELOG.md` and `docs/host-integration.md` for the full walkthrough.

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

The deterministic policy engine returns an action, reason codes, confidence, scope, and policy version. It does not execute tools. A caller may use an LLM for classification, but the final safety gates remain deterministic. Two policy versions ship:

- **`policy-v2`** — the default since 0.2.0. Strictly passive: only acts when inputs are already bad.
- **`policy-v3`** — opt-in since 0.6.0. Adds three "action bias" branches (action pressure, uncertainty threshold, complexity without recent retrieval) that fire *before* the v2 ordering, so a host that loops on silent answers or never reaches for a search on its own gets nudged. See `docs/behavior-policy.md`.

| Action | When it fires |
| --- | --- |
| `answer_directly` | local context is sufficient |
| `retrieve` | time-sensitive, explicit search, context insufficient, or v3 uncertainty threshold hit (remote gated) |
| `retrieve_then_act` | **v3 only.** Action pressure (3 silent answers in a row), or complex task without a recent retrieve. Hosts that don't implement this should map it to `retrieve`. |
| `explore` | **v0.7.0+, v3 only.** Complex or uncertain task, no recent `web_search`, and `remote_allowed` is set. Means "do a ReAct-style loop: web_search → inspect → maybe web_fetch → answer". Hosts that don't implement ReAct should map it to `retrieve_then_act`. |
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

### Built-in web adapters (0.7.0+)

Three web adapters ship in `agent_compass.adapters` and plug into `RetrievalOrchestrator` the same way the local store does:

- `DuckDuckGoAdapter` — stdlib `urllib` HTML scrape of `duckduckgo.com/html/`, no API key, the default.
- `TavilyAdapter` — JSON API at `api.tavily.com`, reads `TAVILY_API_KEY` from the env.
- `WebFetchAdapter` — URL → text + summary, conservative HTML extraction.

All three honour `CompassConfig.remote_allowed` and run every response through `PrivacyBoundary.assert_safe_for_remote` before summarising. PII is redacted, a row whose body contains a *secret* is dropped silently, and `WebFetchAdapter` raises rather than returning a page that contained a secret. Timeouts default to 3 s search / 8 s fetch / 1 retry.

Wiring one in is one line:

```python
from agent_compass import Compass, CompassConfig
from agent_compass.adapters import DuckDuckGoAdapter

compass = Compass(CompassConfig(remote_allowed=True))
compass.retrieval.retrievers.append(DuckDuckGoAdapter(compass.config))
```

The `EXPLORE` action is the one that pulls the trigger — see `docs/behavior-policy.md` for the decision order.

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

### Hooks (0.5.0+)

The example hook set in `hooks/settings.example.json` wires up all five events. Two design points matter:

- **`UserPromptSubmit` writes the `last_task_id` pointer** at `~/.claude/state/last_task_id` via `agent-compass context set`. The `Stop` hook reads that file (or the `AGENT_COMPASS_TASK_ID` env var) so it knows which task to checkpoint — see `task checkpoint --unspecified` below.
- **`PostToolUse` is async** by default. `agent-compass feedback add` appends to `~/.claude/state/feedback_pending.jsonl` and returns 0 immediately, so a sound-reminder hook on the same event no longer races the SQLite write. The `Stop` hook runs `agent-compass feedback flush` to persist the queue. Use `--sync` or set `AGENT_COMPASS_FEEDBACK_SYNC=1` to opt back into synchronous mode.

### `task checkpoint --unspecified`

The `Stop` hook uses `--unspecified` when it has no explicit task id. The CLI resolves the id via this priority chain:

1. The `task_id` positional arg (if provided).
2. The state file at `~/.claude/state/last_task_id` (overridable via `AGENT_COMPASS_CLAUDE_STATE_DIR`).
3. The `AGENT_COMPASS_TASK_ID` env var.
4. The literal string `"unspecified"`, with a stderr warning and a placeholder task created on the fly so the checkpoint can still land.

A `Stop` hook that lands on the literal `"unspecified"` id is a real signal that the host lost its task id somewhere — the warning exists to surface that.

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
