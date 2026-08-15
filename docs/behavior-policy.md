# Behavior Policy (v2 + v3)

Agent Compass ships two policy versions. **v2** is the default and unchanged since
0.2.0; **v3** is opt-in since 0.6.0 and adds "action-bias" branches so an
agent does not get stuck silently answering when the work is actually complex or
uncertain. As of 0.7.0, one of those branches is `EXPLORE` — it escalates to a
web search + ReAct loop when the task looks complex or uncertain and the host
has not yet asked the open web. The version reported on a `Decision` is whatever
rule fired it; a v3 engine that only saw legacy inputs reports `policy-v2` and
behaves identically.

## Decision order

The engine evaluates the rules in order; the first match wins.

### v2 (default, since 0.2.0)

1. `waiting_for_approval` → `pause_for_approval` (`approval_pending`)
2. `external_side_effect` **or** destructive intent → `pause_for_approval`
3. retry budget exhausted → `stop` (`retry_budget_exhausted`)
4. session state is `ending` or `ended` → `consolidate_memory`
5. task in progress, last turn interrupted → `resume` (`task_interrupted`)
6. task in progress, in-session → `continue` (`task_in_progress`)
7. ambiguity ≥ threshold → `ask_user`
8. retrieval needed → `retrieve` (remote gated by config + flag)
9. otherwise → `answer_directly`

### v3 additional branches (opt-in via `policy_v3_enabled`, since 0.6.0)

These slots in *between* steps 5 and 6 of the v2 order, in this sequence:

- **`A. action_pressure`** — `consecutive_answer_directly ≥ action_pressure_threshold` (default 3) → `retrieve_then_act`. If the host has produced three silent answers in a row the engine breaks the loop and demands an outer action before the next prompt.
- **`D. EXPLORE`** *(since 0.7.0)* — `complexity_score ≥ complexity_threshold` OR `uncertainty_score ≥ uncertainty_threshold`, AND no `web_search` in `recent_actions[-5:]`, AND `remote_allowed` set on both the config and the caller's `DecisionContext` → `explore`. The host should run a ReAct-style loop: `web_search` → inspect → maybe `web_fetch` → answer. EXPLORE is the only v3 branch that *requires* `remote_allowed`; it never fires offline.
- **`B. uncertainty_threshold`** — `uncertainty_score ≥ uncertainty_threshold` (default 0.5) → `retrieve`. Fires only when EXPLORE did not (i.e. remote is not allowed, or the host already searched this turn). The host's own self-report overrides the legacy "I have enough context" branch. This is the fix for the "I think I know but I don't" dead loop.
- **`C. complexity_without_recent_retrieval`** — `complexity_score ≥ complexity_threshold` (default 0.6) AND no retrieve-shaped action in `recent_actions[-5:]` → `retrieve_then_act`. Multi-step work should not be answered from the first internal scratchpad, but a host that has *already* gathered (e.g. `recent_action=["retrieve"]`) is left alone. Fires only when EXPLORE did not.

The order within v3 is **A → D → B → C**: *pressure beats the web beats self-doubt beats planning*. Pressure means "do anything" — do not even pause to ask whether a search is needed. The web supersedes local retrieve when remote is actually allowed and the host has not yet searched. Self-doubt and planning only fire when the web path is unavailable. This ordering matches the symptom the design was written against.

A v3 engine that receives a `DecisionContext` with all v3 fields at their
neutral defaults (`complexity_score=0`, `uncertainty_score=0`,
`consecutive_answer_directly=0`, `recent_actions=[]`) matches **no** v3
branch and falls through to the v2 ordering unchanged. Opting in is therefore
zero-cost for hosts that have not yet wired up the new signals.

## Retrieve first

Prefer local context. Retrieve when the user explicitly asks, the question is time-sensitive, or the available context is insufficient. Remote retrieval requires the caller's `remote_allowed` flag **and** the config-level `remote_allowed` (which `config/*.example.yaml` calls `retrieval.remote_requires_explicit_or_high_confidence: false` to enable).

## Ask first

Ask when the goal is materially ambiguous, required input is missing, or a high-impact choice cannot safely use a default. Do not ask users to choose minor reversible formatting or naming details when a project convention is available.

## Pause for approval

Pause before external messages, publishing, deletion, payments, production changes, or other configured side effects. Approval is a state transition (`WAITING_FOR_APPROVAL`), not a prompt suggestion.

The destructive action list is configured via `policy.approval.destructive_actions` in YAML/JSON. The `auto_detect` flag (default on) lets the engine also match the same keywords in the user's `user_input` and `proposed_actions` so callers do not have to compute them upstream.

## Continue to completion

A failed tool call becomes a retry, blocked, or failed state. It never silently becomes completed. When the retry budget is exhausted the engine returns `stop` instead of looping. A task checkpoint contains enough structured state to resume without rereading an unbounded transcript.

## Session lifecycle

`SessionState` is a small state machine the host can report to the engine. When the engine sees `ending` or `ended` it returns `consolidate_memory` so the host can flush learnings before exit.

## Opting in to v3

Three equivalent ways:

```bash
# environment
AGENT_COMPASS_POLICY_V3=true agent-compass decide --input "..."
```

```yaml
# config/*.yaml
policy:
  policy_v3_enabled: true
  complexity_threshold: 0.6
  uncertainty_threshold: 0.5
  action_pressure_threshold: 3
```

```python
from agent_compass import CompassConfig, PolicyEngine
config = CompassConfig(policy_v3_enabled=True)
engine = PolicyEngine(config=config)
```

A host that does not set the four new `DecisionContext` fields still gets v2
behaviour with the same `policy_version` as before. This is the migration
contract: every adopter can flip the gate independently of their context wiring.

## EXPLORE: when the engine asks the open web

`DecisionAction.EXPLORE` is a forward-compatible superset of `retrieve_then_act`.
It means: "the task is complex or uncertain, you have not yet asked the open
web, and the user has granted you network permission — go do a ReAct loop."

A host that does not implement ReAct should map `EXPLORE → RETRIEVE_THEN_ACT`
and the rest of the system keeps working. A host that does implement ReAct
should:

1. Read the reason code in `decision.reason_codes` (`complexity_explore` or
   `uncertainty_explore`) to know which signal tripped.
2. Call the search adapter (see `agent_compass.adapters.web_search`) with the
   original `user_input` as the query.
3. Read the bounded digest; if the answer is in the first 1–2 entries, expand
   the body on demand via `MemoryService.get(memory_id)` (the web adapters use
   the same `memory_id` contract).
4. If still uncertain, call `WebFetchAdapter` on the most relevant URL and
   summarise again.
5. Stop at "good enough" or after the per-task retry budget; do not loop
   silently.

The web adapters (`DuckDuckGoAdapter`, `TavilyAdapter`, `WebFetchAdapter`)
honour `CompassConfig.remote_allowed`. Without that flag the adapter raises
`RemoteNotAllowedError` and the orchestrator records it as a per-source error,
so a missing flag never takes down local recall. The privacy boundary is
applied to every response: PII is redacted, a row whose body contains a
*secret* is dropped silently, and `WebFetchAdapter` raises rather than
returning a page that contains a secret. See `docs/retrieval-orchestration.md`
for the integration details.
