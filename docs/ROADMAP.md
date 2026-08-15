# Agent Compass Roadmap

## Shipped

### v0.7.0 — Built-in web adapter and `EXPLORE` action (2026-08-15)

The last gap in the v0.6.0 story: "v3 can demand an outer action, but the
local store is the only place the action can go." v0.7.0 closes that loop
by shipping web adapters and a new `EXPLORE` action that means "do a
ReAct-style loop: web_search → inspect → maybe web_fetch → answer".

- `agent_compass.adapters.web_search.DuckDuckGoAdapter` — stdlib `urllib`
  HTML scrape of `duckduckgo.com/html/`, no API key, default backend.
- `agent_compass.adapters.web_search.TavilyAdapter` — JSON API at
  `api.tavily.com`, reads `TAVILY_API_KEY` from the env.
- `agent_compass.adapters.web_fetch.WebFetchAdapter` — URL → text +
  summary, conservative HTML extraction.
- All three implement the `Retriever` protocol directly, honour
  `CompassConfig.remote_allowed`, apply `PrivacyBoundary.assert_safe_for_remote`
  to every response, and time out fast (3 s search / 8 s fetch / 1 retry).
- `DecisionAction.EXPLORE = "explore"` — fires before B/C of the v3
  ordering when remote is allowed and the host has not yet searched.
- The v3 branch order is now A → D → B → C: pressure beats the web beats
  self-doubt beats planning.
- Privacy boundary integration: a row whose body contains a *secret* is
  dropped silently; `WebFetchAdapter` raises `WebAdapterError` rather
  than returning a partial page.

See `CHANGELOG.md` and `docs/retrieval-orchestration.md`.

---

### v0.4.0 — Activation-v2 scoring and bounded retrieval (2026-08-10)

Not originally on this roadmap. It jumped the queue because it turned out to
be a prerequisite: a richer memory store makes context overflow *worse*, so
the retrieval bound had to land before we encourage adopters to store more.

- `activation-v2` — opt-in five-dimension scoring with emotion and instinct
  tags and a two-segment retention curve. Per-record `formula_version`
  routing, so nothing migrates on upgrade.
- `agent_compass.retrieval` — Top-K plus token-budget recall that returns
  summaries and never full memory bodies, with every withheld item counted
  and reported.

See `CHANGELOG.md` and `docs/retrieval-orchestration.md`.

---

## v0.5.0 — Adopt the first real user (幻梦)

Target: 2026-08-20 (unchanged). Driven by the day-one report from 幻梦, the
first verified adopter. Scope is unchanged from when this was numbered
v0.4.0; only the version number moved, so that release numbers follow ship
order.

### Hooks: complete the 5-event set

幻梦 wired up three of the five hooks on day one (SessionStart / UserPromptSubmit / PreToolUse) and held back two (`Stop` for `task_checkpoint`, `PostToolUse` for `feedback add`) pending stability observation. v0.5.0 makes both of those safe to install.

**`task_checkpoint` needs a `task_id`** — Claude Code processes do not expose a "current task" concept, so a `Stop` hook has no way to know which task to checkpoint. Fix:

- New `agent-compass context` subcommand writes the current task id to `~/.claude/state/last_task_id` whenever `UserPromptSubmit` fires (no-op if no active task).
- `Stop` hook reads that file first; falls back to the `AGENT_COMPASS_TASK_ID` environment variable; falls back to "unspecified task" with a clear log line.
- The `task_checkpoint` CLI gains an `--unspecified` flag so a `Stop` hook can checkpoint the active task without picking the wrong one.

**`feedback add` should not block the host** — 幻梦 runs a sound reminder hook on the same `PostToolUse` event, and synchronous `feedback add` competes with it. Fix:

- `agent-compass feedback add` becomes async by default: writes to `~/.claude/state/feedback_pending.jsonl` and returns 0 immediately.
- A new `agent-compass feedback flush` reads the pending file, batches entries into `feedback.record` JSONL requests, and persists them via the regular store. Designed to be called from a low-frequency hook (e.g. `Stop`) or a cron.
- An env var `AGENT_COMPASS_FEEDBACK_SYNC=1` opts back into synchronous mode for callers that prefer it.

### Adopter-visible documentation

幻梦 asked for the architecture and behavior-policy docs to be re-readable end to end. v0.5.0 will:

- Add a `docs/adopter-journey.md` walkthrough: clone → install → wire hooks → first doctor → first decide → first task checkpoint → first resume.
- Update `docs/claude-code-integration.md` with the new `last_task_id` flow and the async feedback flush recipe.
- Promote `docs/architecture.md` and `docs/behavior-policy.md` to "must read before reporting feedback" in the README.

### Schemas

- Decision response schema gains an optional `last_task_id` field that the host can echo back, so callers can verify their own bookkeeping.
- A new `context` response schema describes what `last_task_id` was resolved from (`user_prompt`, `env`, `unspecified`).

### Tests

- Hook subprocess tests for all five events, including `Stop` falling back to `last_task_id` and `PostToolUse` not blocking on async feedback.
- Regression: synchronous `feedback add` still works when `AGENT_COMPASS_FEEDBACK_SYNC=1`.
- Golden test for the new `task_checkpoint --unspecified` path.

---

## v0.6.0 — Action bias (policy-v3, opt-in)

Target: 2026-09. Driven by a session with 银月's host agent on 2026-08-15, where
two repeated symptoms surfaced: agents stuck in long "thinking" turns with no
external action, and agents that never reach for a web search on its own.
Both are downstream of the same root: v2 was strictly *passive* — it only
acted when the inputs were already bad. v3 adds three "action bias" branches
that fire *before* the legacy ASK_USER / RETRIEVE / ANSWER_DIRECTLY order.

### What ships in v0.6.0

- **`DecisionAction.RETRIEVE_THEN_ACT`** — a new action that means "retrieve,
  then take at least one tool step before answering". Hosts that do not yet
  honour it can still treat it as `RETRIEVE`; the action is documented as
  forward-compatible.
- **`DecisionContext` gains four fields** (all defaulted to neutral so legacy
  hosts keep working): `complexity_score`, `uncertainty_score`,
  `consecutive_answer_directly`, `recent_actions`.
- **`PolicyEngine.decide()`** gains three new branches, opt-in behind
  `CompassConfig.policy_v3_enabled`:
  - `action_pressure` — three silent `ANSWER_DIRECTLY` in a row →
    `RETRIEVE_THEN_ACT`. Breaks the "thinking without acting" loop.
  - `uncertainty_threshold` — `uncertainty_score ≥ 0.5` → `RETRIEVE`.
    Bypasses the legacy "I have enough context" branch. Fixes "I think I
    know but I don't".
  - `complexity_without_recent_retrieval` — `complexity_score ≥ 0.6` and
    no retrieve-shaped action in `recent_actions[-5:]` → `RETRIEVE_THEN_ACT`.
    Multi-step work should not be answered from the first scratchpad, but a
    host that already gathered is left alone.
- **Config plumbing** — env var `AGENT_COMPASS_POLICY_V3` and YAML key
  `policy.policy_v3_enabled`. Three threshold keys, all overridable. The
  defaults match the values used in the golden tests.
- **CLI surface** — `agent-compass decide` gains `--complexity-score`,
  `--uncertainty-score`, `--consecutive-answer-directly`, `--recent-action`.
- **Schema** — `decision.schema.json` adds `retrieve_then_act` to the action
  enum.
- **Golden tests** — five new fixtures cover the v3 branches and confirm
  v3-disabled stays v2.
- **Backward compatibility** — every v2 golden test still passes unchanged.
  A v3-enabled engine that receives only legacy fields falls through to the
  v2 ordering and reports `policy-v2`. Opting in is zero-cost for hosts
  that have not wired up the new signals.

### Why opt-in, not default

v2 has a real semantic — "wait until the inputs are bad enough". That is the
right default for a host that already knows its job (a deployed agent with a
working tool loop). v3 is a *rescue* mode for hosts whose judgment is
unreliable. Shipping it as opt-in lets v2 adopters keep their current
behaviour while still letting new adopters turn on the rescue mode without a
migration script.

### Schema additions

- `DecisionContext.complexity_score: float = 0.0`
- `DecisionContext.uncertainty_score: float = 0.0`
- `DecisionContext.consecutive_answer_directly: int = 0`
- `DecisionContext.recent_actions: list[str] = []`
- `DecisionAction.RETRIEVE_THEN_ACT = "retrieve_then_act"`
- `CompassConfig.policy_v3_enabled: bool = False`
- `CompassConfig.complexity_threshold: float = 0.6`
- `CompassConfig.uncertainty_threshold: float = 0.5`
- `CompassConfig.action_pressure_threshold: int = 3`

### Tests

- Golden: `v3_action_pressure`, `v3_uncertainty_bypasses_sufficient_context`,
  `v3_complexity_with_recent_retrieval_does_not_fire`,
  `v3_complexity_without_recent_retrieval_fires`,
  `v3_disabled_degrades_to_v2`.
- Unit: `_retrieved_recently()` heuristic on synthetic `recent_actions`.

---

## v0.7.0 — Built-in web adapter and `EXPLORE` action

*Shipped 2026-08-15. See "Shipped" section above. The plan that follows is
kept for historical context.*

Target: 2026-10. Once v0.6.0 ships, the policy can already demand a retrieve
that hits *something*. Today "something" is only the local memory store; v0.7.0
ships a default web adapter so `RETRIEVE_THEN_ACT` can actually leave the
machine.

### Built-in `WebSearchAdapter`

- A new `agent_compass.adapters.web_search` module ships a `DuckDuckGoAdapter`
  (HTML scrape, no API key) as the default, and a `TavilyAdapter` (requires
  `TAVILY_API_KEY`) for users who want a managed backend.
- A new `WebFetchAdapter` (already-validated URL → text + summary) joins the
  retrieval orchestrator the same way `CallableRetriever` does today.
- Both honour `CompassConfig.remote_allowed`. Without it the adapter raises
  and the orchestrator records it as a source error — exactly like the
  per-source error path that already exists.
- Timeouts default to 3 s for search, 8 s for fetch, with one retry. Both
  are configurable via `policy.retrieval.web_*` keys.

### `EXPLORE` action

- `DecisionAction.EXPLORE = "explore"` joins `RETRIEVE_THEN_ACT`. It means
  "do a ReAct-style loop: web_search → inspect → maybe web_fetch → answer".
- The decision engine emits `EXPLORE` when:
  - v3 is enabled,
  - `complexity_score ≥ complexity_threshold` **or**
    `uncertainty_score ≥ uncertainty_threshold`, AND
  - `recent_actions` shows no `web_search` in the last 5 steps.
- Hosts that do not implement ReAct can map `EXPLORE → RETRIEVE_THEN_ACT`.

### Privacy boundary integration

- All web responses flow through `agent_compass.privacy.boundary` before
  being summarised. PII the detector catches is redacted; the host is told
  the redaction happened so it does not trust the trimmed text.
- `EXPLORE` is the only new action that *requires* `remote_allowed`. The
  other v3 branches keep working offline.

### Tests

- Golden fixtures for `EXPLORE`: forced when complexity + uncertainty both
  fire, suppressed when `recent_actions` shows a recent `web_search`.
- Adapter tests with a recorded HTTP fixture (the existing
  `test_redaction.py` style) — no real network calls in CI.
- Property test: a v3 engine with a fake adapter returns `EXPLORE` on the
  same inputs that without the adapter return `RETRIEVE_THEN_ACT`.

---

## v0.6.1+ and beyond (informational)

These were sketched for v0.6.0 before the action-bias work displaced them;
kept here so the work is not lost.

### Shared method pack (was v0.6.0)

幻梦 and 银月 are now both running `agent-compass`. The natural next step is to
ship a `.skills/yinyue-methods/` (and later `huanmeng-methods/`) pack that
lets any adopter adopt proven retrieval / scoring / token-saving techniques
without re-deriving them.

- Document a `skill_pack` schema: a folder with `SKILL.md`, `README.md`, and
  optional code templates.
- Ship one example pack covering 双层检索 (index match → rerank → Top-7
  detail), `activation-v2` scoring, and the token-saving rule set.

### Cross-session synthesis

- A local-only, deterministic feedback digest: per-task-id counts of
  `positive` / `negative` / `neutral`, exposed via
  `agent-compass feedback stats --json`.
- A `feedback trend` view that flags tasks where the negative ratio is
  climbing over consecutive checkpoints, so a `UserPromptSubmit` decision
  can downgrade its confidence automatically.

### Adopter loop

Once v0.5.0 ships and 幻梦 installs the remaining hooks, the README "First
adopter" block becomes a template: every verified adopter appends a
five-bullet report. v0.6.0+ turns this into a small public ledger under
`docs/adopters/`.

---

## Non-goals (still)

These were promised in the README and remain:

- No consciousness, subjective experience, or autonomous life.
- No online model training or remote transmission of secrets.
- No silent infinite retry — `STOP` is a real action.
- No permission to skip human approval for high-impact actions.

The privacy detector remains a baseline, not a complete DLP product. Domain-specific secrets still need domain-specific detectors.

---

*Updated 2026-08-10: v0.4.0 shipped as scoring + retrieval; the adopter-hooks release moved to v0.5.0 with its scope and target date unchanged.*