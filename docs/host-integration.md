# Host Integration

> A walkthrough for an agent (or an agent's operator) that wants to
> get v3 / web / hooks with the minimum amount of reading.

This document assumes the agent has Python 3.10+, the project is
checked out, and the operator can run `pip install -e .` from the
repo root. A host that needs the v0.6.0+v0.7.0 features without
reading three CHANGELOG entries should follow the recipe below.

## 0. Install

```bash
git clone https://github.com/hanli1999/agent-compass
cd agent-compass
python -m pip install -e '.[dev]'
agent-compass doctor
```

`doctor` should print `ok: true` and a path under your home
directory. If it does not, fix the data-directory path before
proceeding — the rest of the integration is useless without a
working store.

## 1. The four-line host loop

The whole point of v0.8.0 is that a host can stop reading
CHANGELOGs. The recipe below is everything a new host needs:

```python
from agent_compass import Compass
from agent_compass.runtime import (
    apply_smart_defaults,
    build_smart_default_config,
    HostLoop,
)

compass = Compass(build_smart_default_config(remote_allowed=True))
apply_smart_defaults(compass)        # v3 on, web adapter wired
loop = HostLoop(compass)
```

After this:

- `compass.config.policy_v3_enabled` is `True`.
- `compass.retrieval.retrievers` contains a `DuckDuckGoAdapter`
  whose `name` is `"web_search_ddg"`.
- `loop.tracker` is ready to count silent answers and remember
  recent tool calls.

If `remote_allowed` is `False` (the default), the web adapter is
*not* wired and `EXPLORE` will not fire. That is the correct
default for offline hosts; flipping the flag is the only step
needed to enable the open-web rescue mode.

## 2. The decision loop

A host loop calls two methods, full stop:

```python
def on_tool_call(name: str) -> None:
    loop.record(name)        # after a tool call

def on_user_prompt(prompt: str) -> Decision:
    decision = loop.decide(prompt)
    if decision.action is DecisionAction.ANSWER_DIRECTLY:
        # The engine is happy; speak.
        speak(...)
    elif decision.action is DecisionAction.RETRIEVE:
        # Local memory has something. Pull summaries.
        items = compass.recall(prompt)
        speak(format_items(items))
    elif decision.action is DecisionAction.RETRIEVE_THEN_ACT:
        # Engine says: gather, then act. Pull memory and run at
        # least one tool step before speaking.
        items = compass.recall(prompt)
        # ... do something with the items, then speak ...
    elif decision.action is DecisionAction.EXPLORE:
        # Engine says: go to the web, then act.
        search_results = compass.retrieval.retrieve(prompt)
        for item in search_results:
            if item.source.startswith("web_"):
                # ... inspect, maybe web_fetch, then speak ...
                pass
    elif decision.action is DecisionAction.ASK_USER:
        # The prompt is ambiguous. Ask.
        ask_user(...)
    elif decision.action is DecisionAction.PAUSE_FOR_APPROVAL:
        # The action is destructive. Wait.
        wait_for_approval(...)
    elif decision.action is DecisionAction.STOP:
        # Retry budget exhausted. Surface the error and stop.
        return
    elif decision.action is DecisionAction.RESUME:
        # Task was interrupted; resume from checkpoint.
        resume(...)
    elif decision.action is DecisionAction.CONSOLIDATE_MEMORY:
        # Session is ending; flush learnings.
        consolidate(...)
    elif decision.action is DecisionAction.CONTINUE:
        # Task in progress; keep going.
        pass
    return decision
```

That is the entire surface. The host does not maintain
`consecutive_answer_directly`, does not know about
`complexity_score`, does not track `recent_actions` —
`HostLoop.tracker` does it.

## 3. The four v3 fields, demystified

`AutoTracker` maintains four fields. The host does not compute
them; the host sets `complexity` and `uncertainty` from its own
self-report, and lets the tracker do the rest.

| Field | What it means | Who sets it |
| --- | --- | --- |
| `consecutive_answer_directly` | "How many text-only answers have I given in a row, with no tool call in between?" | tracker increments on `record_answer`, resets on `record_action` |
| `recent_actions` | "The last 5 (or 20) tool calls I have made." | tracker appends on every `record_action` |
| `complexity_score` | "How complex is the current task, in [0, 1]?" | host, via `tracker.set_complexity(0.8)` |
| `uncertainty_score` | "How confident am I in my own answer, in [0, 1]?" | host, via `tracker.set_uncertainty(0.6)` |

The first two are mechanical. The second two are subjective and
**must** be honest. A host that reports `complexity_score=0.1`
on a multi-step migration gets `answer_directly` from the
engine, which is the wrong answer. A host that reports
`uncertainty_score=0.05` because "I'm sure" is the host that
will silently produce a wrong answer. The whole v3 design
assumes honest self-report.

A practical heuristic for a model that does not already
self-evaluate:

- `complexity_score = 0.0` for single-line edits, `0.5` for
  multi-file changes, `0.9` for migrations or refactors.
- `uncertainty_score = 0.0` for "the user gave me a complete
  spec", `0.5` for "I am working from a partial prompt",
  `0.9` for "I have to look something up".

These are not measured constants; they are exposed as plain
`CompassConfig` keys precisely so a host can disagree.

## 4. Web adapters

The three shipped adapters:

| Adapter | When to use | API key |
| --- | --- | --- |
| `DuckDuckGoAdapter` | default; no account needed | none |
| `TavilyAdapter` | managed backend, low variance | `TAVILY_API_KEY` |
| `WebFetchAdapter` | pull a single URL into the same `RetrievedItem` shape | none |

To swap the default `DuckDuckGoAdapter` for `TavilyAdapter`:

```python
from agent_compass.adapters import DuckDuckGoAdapter, TavilyAdapter

compass.retrieval.retrievers = [
    r for r in compass.retrieval.retrievers
    if getattr(r, "name", "") != "web_search_ddg"
]
compass.retrieval.retrievers.append(TavilyAdapter(compass.config))
```

The privacy boundary is applied to every response. A row whose
body contains a *secret* is dropped silently; a `WebFetchAdapter`
that pulls a secret-bearing page raises `WebAdapterError`. The
detector is the same baseline the local store uses, not a
complete DLP product.

## 5. Hooks (Claude Code)

The cold-start path is one call:

```python
from agent_compass.runtime import install_claude_code_hooks

report = install_claude_code_hooks()
print(report.to_dict())
```

The installer writes the five hook events to
`~/.claude/settings.json`. Existing entries are preserved;
ours are appended. The `Stop` hook runs both `task checkpoint
--unspecified` and `feedback flush` in sequence, so the
async-feedback queue is drained at the end of every session.

For a power user that already has a `settings.json` and wants
to merge by hand, see `hooks/settings.example.json` in the
repo. The commands are the same ones the installer writes.

## 6. Privacy

`Compass.privacy` is a `PrivacyBoundary` instance. Use it to
scan any text that might leave the host:

```python
safe = compass.privacy.assert_safe_for_remote("contact alice@example.com")
# "[REDACTED:email]" → "alice@example.com" got replaced
```

`assert_safe_for_remote` raises on *secrets* (private keys,
JWTs, bearer tokens, API keys, passwords, SSH keys) and
redacts *sensitive* (emails, IPs, mainland China phones/IDs,
absolute paths, user-at-host). A host that writes a memory
proposal that contains a secret is rejected at the
`MemoryService.propose` boundary; a host that tries to send
a secret over the network via the web adapters is rejected
by the adapter.

## 7. Verification

The headline test in `tests/unit/test_runtime.py` exercises the
whole recipe end to end:

```python
def test_fresh_host_uses_v3_and_web_out_of_the_box(tmp_path):
    from agent_compass import Compass
    from agent_compass.runtime import apply_smart_defaults, build_smart_default_config, HostLoop

    compass = Compass(build_smart_default_config(data_dir=tmp_path, remote_allowed=True))
    apply_smart_defaults(compass)
    assert compass.config.policy_v3_enabled is True
    assert any(getattr(r, "name", "") == "web_search_ddg"
               for r in compass.retrieval.retrievers)

    loop = HostLoop(compass)
    loop.record("answer"); loop.record("answer"); loop.record("answer")
    decision = loop.decide("what now")
    assert decision.action is DecisionAction.RETRIEVE_THEN_ACT
    assert "action_pressure" in decision.reason_codes

    decision = loop.decide("what changed in fastapi 0.118",
                           complexity=0.9, remote_allowed=True, has_sufficient_context=True)
    assert decision.action is DecisionAction.EXPLORE
```

A host that runs this against its own install (substituting
its real `data_dir`) is the cheapest way to confirm the
integration is alive.

## 8. When *not* to use this

- A host that is purely offline and *never* wants the web
  rescue mode should leave `remote_allowed=False` and skip
  `apply_smart_defaults` (or set `force=False` and call it
  with `policy_v3_enabled=False` already set). The framework
  still works; the engine just never emits `EXPLORE`.
- A host that has its own complexity / uncertainty estimation
  pipeline can ignore `tracker.set_complexity` /
  `tracker.set_uncertainty` and pass overrides to
  `loop.decide(..., complexity=X, uncertainty=Y)` per call.
  The tracker becomes a pure recent-actions / silence counter.
- A host that wants to reason about *why* a decision fired
  can call `loop.explain()` at any time; the returned dict
  is JSON-serialisable and safe to log.

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `agent-compass doctor` says `ok: false` | data dir is read-only | set `AGENT_COMPASS_DATA_DIR=/path/writable` |
| v3 branches never fire | `policy_v3_enabled` is False | call `apply_smart_defaults(compass)` |
| `EXPLORE` never fires | `remote_allowed` is False on either side | set `CompassConfig(remote_allowed=True)` and pass `remote_allowed=True` to `decide` |
| Web adapter always returns empty | DDG captcha, or no `TAVILY_API_KEY` | swap to a different adapter; check the env var |
| `task checkpoint` writes to "unspecified" | the host lost the `last_task_id` pointer | make sure `agent-compass context set` is in the `UserPromptSubmit` hook |
| `feedback add` events are missing | the `Stop` hook is not running `feedback flush` | add `agent-compass feedback flush` to the `Stop` hook |

## 10. What lands in 0.8.0 (this version)

- `agent_compass.runtime` package
- `AutoTracker` (the silent-answer counter and recent-actions window)
- `HostLoop` (the four-line wrapper)
- `apply_smart_defaults` and `build_smart_default_config`
- `install_claude_code_hooks` (the cold-start hooks installer)
- 30 new unit tests

What does *not* land in 0.8.0:

- An agent-execution layer. `HostLoop` tells the host what to do
  but does not do it. A host that wants ReAct (web search →
  inspect → maybe fetch → answer) wires that itself; the
  engine just emits `EXPLORE`.
- A model-assisted self-evaluator for `complexity_score` /
  `uncertainty_score`. The host sets those honestly; the
  framework holds the values.
- A persistence layer for `AutoTracker`. The tracker is
  in-memory only. A host that wants to remember silence /
  recent actions across sessions writes them to its own
  sidecar.
