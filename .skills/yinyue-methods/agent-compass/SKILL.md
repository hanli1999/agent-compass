---
name: agent-compass
description: Drop-in host-side SDK for Agent Compass (v0.8.0+). Use when an agent loop needs to decide when to retrieve / ask / answer / stop / consolidate, when silent-thinking loops need breaking, when the open-web rescue mode should fire, or when a Claude Code operator wants the five hooks wired without a manual merge. Activates on phrases like "use agent-compass", "decide what to do next", "add compass to my loop", "install the compass hooks".
---

# Agent-Compass SDK — host-side glue (v0.8.0+)

This skill packages the four-line recipe that turns Agent Compass into
a self-driving loop. It exists so a fresh host gets v3 enabled, the
four v3 fields auto-tracked, and `DuckDuckGoAdapter` wired in — without
reading three CHANGELOG entries.

## When to activate

- The host is running an agent loop and needs the next action.
- Silent-thinking loops (three text-only answers in a row with no tool
  call) need to be broken.
- A complex task is being answered without any retrieval.
- The Claude Code operator wants the five hooks (SessionStart /
  UserPromptSubmit / PreToolUse / PostToolUse / Stop) wired without a
  manual merge.

## When *not* to activate

- The host is purely offline and the open-web rescue mode is forbidden
  (do not call `apply_smart_defaults`, or pass `policy_v3_enabled=False`
  before calling it).
- The host already maintains its own `complexity_score` / `uncertainty_score`
  pipeline and prefers to pass those per-call rather than let the tracker
  hold them.
- The task is trivial enough that a single tool call is the whole story
  (the SDK overhead is not worth it).

## The four-line recipe

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

`remote_allowed=False` (the default) leaves the web adapter unwired and
`EXPLORE` will not fire. That is the correct default for offline hosts;
flipping the flag is the only step needed to enable the rescue mode.

## The decision loop

Two methods, full stop:

```python
def on_tool_call(name: str) -> None:
    loop.record(name)        # after a tool call

def on_user_prompt(prompt: str) -> Decision:
    decision = loop.decide(prompt)
    # Branch on decision.action — see SKILL.md §"Decision branches" below.
    return decision
```

The host does not maintain `consecutive_answer_directly`, does not know
about `complexity_score`, does not track `recent_actions` — `HostLoop.tracker`
does it.

## Decision branches (all 10)

| `DecisionAction`             | What it means                                  | Host should                       |
| ---------------------------- | ---------------------------------------------- | --------------------------------- |
| `ANSWER_DIRECTLY`            | local context is sufficient                    | speak                             |
| `RETRIEVE`                   | local memory has something                     | recall + speak                     |
| `RETRIEVE_THEN_ACT`          | gather, then take a tool step                  | recall + tool + speak             |
| `EXPLORE`                    | go to the web, then act                        | web_search + maybe web_fetch + speak |
| `ASK_USER`                   | prompt is ambiguous                            | ask                               |
| `PAUSE_FOR_APPROVAL`         | action is destructive                          | wait                              |
| `STOP`                       | retry budget exhausted                         | surface + stop                    |
| `RESUME`                     | task was interrupted                           | resume from checkpoint            |
| `CONSOLIDATE_MEMORY`         | session is ending                              | flush learnings                   |
| `CONTINUE`                   | task in progress                               | keep going                        |

A full branch-by-branch Python skeleton lives in `recipes/host_loop.py`.

## The four v3 fields

`AutoTracker` maintains four fields. The host does not compute them;
the host sets `complexity` and `uncertainty` from its own self-report,
and lets the tracker do the rest.

| Field                          | What it means                              | Who sets it                                 |
| ------------------------------ | ------------------------------------------ | ------------------------------------------- |
| `consecutive_answer_directly`  | silent-thinking counter                    | tracker: increments on `record_answer`      |
| `recent_actions`               | last 5 (or 20) tool calls                  | tracker: appends on `record_action`         |
| `complexity_score`             | how complex is the current task [0, 1]     | host: `tracker.set_complexity(0.8)`         |
| `uncertainty_score`            | how confident in own answer [0, 1]         | host: `tracker.set_uncertainty(0.6)`        |

Honest self-report is the design contract. A host that reports
`complexity_score=0.1` on a multi-step migration gets `answer_directly`
from the engine, which is the wrong answer.

## Hooks (Claude Code)

Cold-start install in one call:

```python
from agent_compass.runtime import install_claude_code_hooks

report = install_claude_code_hooks()
print(report.to_dict())
```

Writes the five events to `~/.claude/settings.json`. Existing entries
are preserved; ours are appended.

## Privacy boundary

`Compass.privacy` is a `PrivacyBoundary` instance. Use it to scan any
text that might leave the host:

```python
safe = compass.privacy.assert_safe_for_remote("contact alice@example.com")
# "[REDACTED:email]" -> alice@example.com got replaced
```

Secrets (private keys, JWTs, bearer tokens, API keys, passwords, SSH
keys) raise. Sensitive (emails, IPs, mainland China phones/IDs,
absolute paths, user-at-host) is redacted.

## Verification

Run `verify.py` to confirm the integration is alive:

```bash
python verify.py
```

This is the headline test from `tests/unit/test_runtime.py`, shipped as
a stand-alone script so any adopter can run it against their install.

## Files in this pack

- `SKILL.md` — this file.
- `README.md` — human-readable overview.
- `recipes/host_loop.py` — the four-line recipe + every branch handled.
- `recipes/hooks_install.py` — bare hooks-install call.
- `verify.py` — the headline verification test as a runnable script.

## Honest limits

- The SDK is a helper, not a turnkey agent. It tells the host what to
  do but does not run tools.
- `apply_smart_defaults` is one-shot bootstrap. A host that wants v3
  off permanently should set it after the call.
- The hooks installer is for cold-start; power users merge by hand.
- The privacy detector is a baseline, not a complete DLP product.

## Persistence (v0.9.3+)

The `AutoTracker` is in-memory by default. A host that wants the
silent-thinking counter and the recent-actions window to survive a
session restart calls `flush_to(path)` from a Stop hook and
`restore_from(path)` from a SessionStart hook:

```python
# SessionStart
loop.tracker.restore_from("~/.claude/state/tracker.json")

# Stop (after the host is done recording)
loop.tracker.flush_to("~/.claude/state/tracker.json")
```

The format is a single-line JSON object with a `schema_version`
field; unknown versions raise `ValueError` instead of silently
re-scoring. Atomic write (`path + ".tmp"` then rename) means a
crash mid-flush cannot leave the host with a half-written state.

`HostLoop` does not auto-flush. The host picks the moment — typical
choices are the Stop hook (cheap, one write per session) or every
N records (more granular, more I/O).

### CLI hooks (v0.9.4+)

`install_claude_code_hooks()` appends `agent-compass tracker restore`
to `SessionStart` (after `doctor`) and `agent-compass tracker flush`
to `Stop` (after `feedback flush`). A host that runs the installer
once gets cross-session v3 state with no extra wiring. Override the
default path with `--path` on either command (defaults to
`<data_dir>/state/tracker.json`).

## Related

- `docs/host-integration.md` — the long-form walkthrough that this
  skill packs.
- `CHANGELOG.md` — what landed in v0.8.0 and v0.9.0.
- `docs/behavior-policy.md` — the full v3 rule set.