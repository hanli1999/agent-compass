# Behavior Policy (v2 + v3)

Agent Compass ships two policy versions. **v2** is the default and unchanged since
0.2.0; **v3** is opt-in since 0.6.0 and adds three "action-bias" branches so an
agent does not get stuck silently answering when the work is actually complex or
uncertain. The version reported on a `Decision` is whatever rule fired it; a
v3 engine that only saw legacy inputs reports `policy-v2` and behaves
identically.

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

These slots in *between* steps 5 and 6 of the v2 order:

- **`A. action_pressure`** — `consecutive_answer_directly ≥ action_pressure_threshold` (default 3) → `retrieve_then_act`. If the host has produced three silent answers in a row the engine breaks the loop and demands an outer action before the next prompt.
- **`B. uncertainty_threshold`** — `uncertainty_score ≥ uncertainty_threshold` (default 0.5) → `retrieve`. The host's own self-report overrides the legacy "I have enough context" branch. This is the fix for the "I think I know but I don't" dead loop.
- **`C. complexity_without_recent_retrieval`** — `complexity_score ≥ complexity_threshold` (default 0.6) AND no retrieve-shaped action in `recent_actions[-5:]` → `retrieve_then_act`. Multi-step work should not be answered from the first internal scratchpad, but a host that has *already* gathered (e.g. `recent_action=["retrieve"]`) is left alone.

The order within v3 (pressure → uncertainty → complexity) is deliberate:
*pressure beats self-doubt beats planning*. If the user has been silent for too
long, take an action; if you admit you don't know, retrieve; if the plan is
big but you've already searched, answer. This ordering matches the symptom
the design was written against.

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
