# Behavior Policy (v2)

## Decision order

The engine evaluates the following rules in order; the first match wins.

1. `waiting_for_approval` → `pause_for_approval` (`approval_pending`)
2. `external_side_effect` **or** destructive intent → `pause_for_approval`
3. retry budget exhausted → `stop` (`retry_budget_exhausted`)
4. session state is `ending` or `ended` → `consolidate_memory`
5. task in progress, last turn interrupted → `resume` (`task_interrupted`)
6. task in progress, in-session → `continue` (`task_in_progress`)
7. ambiguity ≥ threshold → `ask_user`
8. retrieval needed → `retrieve` (remote gated by config + flag)
9. otherwise → `answer_directly`

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
