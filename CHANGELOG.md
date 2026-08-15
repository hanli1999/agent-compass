# Changelog

## 0.9.3 (unreleased)

### Added
- **`AutoTracker` persistence** (v0.9.3+, opt-in) — `flush_to(path)` /
  `restore_from(path)` round-trip the four v3 fields as a single-line
  JSON object. Atomic write (`path + ".tmp"` then rename) so a crash
  mid-flush cannot leave a half-written state file. `restore_from` is
  a no-op when the file does not exist. `to_dict` / `from_dict` expose
  the format for hosts that want to embed the snapshot in their own
  JSON sidecar.
- **`schema_version` field** in the snapshot payload. Unknown versions
  raise `ValueError` instead of silently re-scoring. Future format
  changes can detect a mismatch and refuse to load, rather than
  producing wrong numbers from a half-recognized blob.
- **Skill pack doc** — `SKILL.md` gains a "Persistence" section so the
  recipe is discoverable from the pack, not just the long-form doc.

### Tests
- `tests/unit/test_runtime.py` gains six persistence tests:
  `test_tracker_to_dict_round_trip`, `test_tracker_flush_to_creates_parent_dir`
  (no leftover `.tmp` after atomic rename),
  `test_tracker_restore_from_returns_false_when_missing`,
  `test_tracker_flush_then_restore_round_trip` (the headline case:
  fresh `AutoTracker()` after a flush picks up the silence counter
  and the two scores), `test_tracker_from_dict_rejects_unknown_schema_version`,
  and `test_tracker_from_dict_tolerates_missing_keys`.

### Still honest about
- `HostLoop` does not auto-flush. A host that wants persistence across
  sessions wires `restore_from` into SessionStart and `flush_to` into
  Stop (or any other timing that fits the host loop). The SDK ships
  the primitives; the timing is the host's call.
- The in-memory tracker is still the default. `apply_smart_defaults`
  does not opt the host into persistence.
- Agent Compass still does not provide consciousness, subjective
  experience, true autonomous life, or permission to skip human
  approval for high-impact actions.

---

## 0.9.2 (2026-08-15)

### Added
- **REPL v3 commands** (`agent_compass.repl.CompassRepl`) — interactive v3 state inspection. The REPL auto-wires a `HostLoop` when `compass.config.policy_v3_enabled` is on; v2 sessions skip the wiring entirely and report `v3 not enabled` for the v3 commands.
  - `state` — JSON view of the tracker snapshot (silent-thinking counter, recent actions window, complexity / uncertainty scores) plus the last decision.
  - `record <name|answer>` — record a tool call (any string) or an `answer`. Mirrors the host-loop's `record()` semantics.
  - `set_complexity <0..1>` and `set_uncertainty <0..1>` — clamp and persist the host-reported score on the tracker.
  - `reset_tracker` — drop everything back to neutral.
  - `decide` gains `--complexity`, `--uncertainty`, `--consecutive-answer`, `--recent-action` for one-off v3 overrides. When v3 is wired, `decide` routes through the loop so the tracker's snapshot is folded into the `DecisionContext`.
- **Backward compat** — v2 REPL sessions see no `state` / `record` / `set_complexity` / `set_uncertainty` / `reset_tracker` in `help`. The 13 pre-existing REPL tests still pass.

### Tests
- `tests/unit/test_repl.py` gains 12 tests covering v3 REPL: friendly v3-not-enabled error in v2 sessions, `state` JSON shape, three-`answer` `record` triggering `action_pressure`, `record <tool>` resetting silence, complexity / uncertainty persistence and clamping, `reset_tracker`, `help` listing v3 commands, end-to-end EXPLORE on `set_complexity 0.9` + `decide`.

### Still honest about
- REPL v3 commands only work when the underlying compass has v3 enabled. The CLI subcommand `agent-compass repl` is unchanged — set `AGENT_COMPASS_POLICY_V3=true` or `apply_smart_defaults(compass)` before launching to opt in.
- `record <name>` accepts any string as the action name. The tracker does not validate against an allow-list; the engine is permissive on purpose so a host can add new tool names without SDK changes.
- The in-memory tracker still does not persist across REPL sessions. Persistence is the host's job (and is tracked in the v0.8.0 "what does not land" list).
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.9.1 (2026-08-15)

### Changed
- **`HostLoop.decide()` auto-injects `remote_allowed`** from `compass.config.remote_allowed` into the `DecisionContext` when the caller did not pass it in `overrides`. A host that forgets the flag now gets `EXPLORE` on a complex remote task instead of being silently downgraded to `RETRIEVE_THEN_ACT`. Callers can still override with `remote_allowed=False` (e.g. on a transient network outage) — the auto-inject only fires when the caller did not specify.
- **Recipe simplification** — `recipes/host_loop.py`'s `on_user_prompt` no longer needs the manual `remote_allowed` injection. The SDK does it. Four-line recipe is now genuinely four lines.

### Tests
- `tests/unit/test_runtime.py` gains three tests: `test_host_loop_auto_injects_remote_allowed` (the headline behaviour), `test_host_loop_caller_can_override_remote_allowed` (explicit `False` blocks `EXPLORE`), and `test_host_loop_does_not_inject_when_remote_blocked` (offline hosts stay offline).

### Still honest about
- The auto-inject is for `remote_allowed` only. Every other `DecisionContext` field still uses the caller's value or the neutral default. A host that wants the engine to consult `complexity_score` / `uncertainty_score` it has not set still has to pass them (or use `tracker.set_complexity` / `set_uncertainty`).
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.9.0 (2026-08-15)

### Added
- **`docs/host-integration.md`** — a 360-line walkthrough that takes a fresh host from `pip install -e .` to a verified loop in one read. Covers install, the four-line host loop, every `DecisionAction` branch handled, the four v3 fields with honest-self-report guidance, web adapter swapping, the cold-start hooks installer, the privacy boundary, the headline verification test, and a troubleshooting table.
- **`.skills/yinyue-methods/agent-compass/`** — a Claude skill pack that bundles the v0.8.0 SDK recipe into one installable artefact. `SKILL.md` (decision-branch table + when-to-use guidance), `README.md`, `recipes/host_loop.py` (full skeleton, every `DecisionAction` handled), `recipes/hooks_install.py` (bare installer call), and `verify.py` (the headline test as a runnable script). Adopters run `python verify.py` to confirm the SDK is alive before adapting the recipe.
- **README pointer** — the v0.8.0 host-side helper block now references `docs/host-integration.md` directly instead of "(when it lands)".

### Dogfood (silver-moon's own run, 2026-08-15)
- `verify.py` reports `OK — agent-compass SDK is alive` against the local install.
- `recipes/host_loop.py --input "what changed in fastapi 0.118" --complexity 0.9` produces `action: explore`, `reason_codes: [complexity_explore, complexity=0.90, uncertainty=0.00]`. The full EXPLORE branch fires on a fresh host without the operator having to thread any flag by hand, as long as `compass.config.remote_allowed` is True.
- One friction point surfaced: `HostLoop.decide()` does not auto-inject `compass.config.remote_allowed` into `DecisionContext.remote_allowed`. The recipe's `on_user_prompt` wrapper now does it for the host, so an adopter that forgets the flag still gets EXPLORE on a complex remote task. Not fixed at the SDK layer in this release because the explicit-flag design is the documented contract; the recipe is the safety net.

### Still honest about
- The runtime helper is a four-line wrapper, not a turnkey agent. The doc names the things it does *not* do (no ReAct execution, no model-assisted self-evaluator, no cross-session `AutoTracker` persistence).
- The `HostLoop.decide()` signature requires the host to pass `remote_allowed` (or to read the recipe's wrapper). This is by design — call-site remote is the policy boundary — but it is friction for new adopters. The recipe documents it; a future release may auto-inject.
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.8.0 (2026-08-15)

### Added
- **`agent_compass.runtime` package** — the host-side glue that turns Agent Compass into a self-driving loop. A fresh host that follows the recipe gets v3 enabled, the four v3 fields auto-tracked, and the `DuckDuckGoAdapter` wired in — no manual wiring required.
  - **`AutoTracker`** (`runtime.tracker`) — in-memory state for the four v3 fields. The host only has to call `record_action(name)` when it calls a tool, and `record_answer()` when it speaks. `record_action` resets the silent-thinking counter; `record_answer` increments it. `set_complexity` / `set_uncertainty` clamp to `[0, 1]`. `snapshot(complexity=..., uncertainty=...)` returns a frozen `TrackerSnapshot` for `DecisionContext`.
  - **`HostLoop`** (`runtime.loop`) — wraps a `Compass` with an `AutoTracker`. `decide(user_input, **overrides)` folds the tracker's snapshot into `DecisionContext`, calls the engine, mirrors the decision back into the tracker, and caches the last decision. `record(kind)` routes `kind="answer"` to `record_answer` and everything else to `record_action`. `explain()` returns a JSON-serialisable view of the loop's current state.
  - **`apply_smart_defaults(compass, *, force=False)`** (`runtime.defaults`) — idempotently flips `policy_v3_enabled` on, sets the three thresholds to their default values, and wires the `DuckDuckGoAdapter` if `remote_allowed`. Returns a `dict` of changes (empty when the call was a no-op). `force=True` overrides any explicit opt-out.
  - **`build_smart_default_config(base=None, *, remote_allowed=None, data_dir=None)`** — one-call constructor for hosts that want the smart defaults baked in from the start.
  - **`install_claude_code_hooks(settings_path=None, *, overwrite=False)`** (`runtime.hooks_install`) — writes the five Claude Code hook events (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`) to `~/.claude/settings.json` (or a path the caller picks). Additive by default: existing hooks are preserved, our entries are appended. Returns a `HookInstallReport` so the operator (or `doctor`) can confirm what landed.
- **The "fresh host has v3 + web" headline test** (`tests/unit/test_runtime.py::test_fresh_host_uses_v3_and_web_out_of_the_box`) — proves the end-to-end story: a host that does the four-line recipe gets action-bias enabled, a web adapter wired, and `EXPLORE` firing on the same inputs that would have been silent under v2.

### Changed
- Nothing in the existing public API. The runtime is purely additive. A host that did not import `agent_compass.runtime` is unaffected.

### Still honest about
- The runtime is a *helper*, not a replacement for a real agent loop. It does not run tools; it tells the host what to do and remembers what just happened. A host that never calls `record` after a tool use will still see a silent-thinking counter.
- `apply_smart_defaults` is a one-shot bootstrap. It does not track "the user manually set v3 back to False after I flipped it on"; the second call is a no-op because the gate is already True. A host that wants v3 off permanently should set it after the call.
- The hooks installer writes a *starter* set. A power user will want to merge by hand; the installer is for the cold-start case where there is no settings.json yet.
- The privacy detector is still a **baseline**, not a complete DLP product.
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.7.0 (2026-08-15)

### Added
- **Built-in web adapters** (`agent_compass.adapters.web_search`, `agent_compass.adapters.web_fetch`).
  - **`DuckDuckGoAdapter`** — stdlib `urllib` HTML scrape of `duckduckgo.com/html/`. No API key, no SDK. Default backend.
  - **`TavilyAdapter`** — JSON API at `api.tavily.com`. Reads `TAVILY_API_KEY` from the env. Recommended for stable, low-variance output.
  - **`WebFetchAdapter`** — URL → text + summary. Conservative HTML extraction, no JS, no DOM rebuilding.
  - All three implement the existing `Retriever` protocol directly, so they slot into `RetrievalOrchestrator` like `CallableRetriever` does. No wrapper.
  - All three honour `CompassConfig.remote_allowed`. Without it the adapter raises `RemoteNotAllowedError` and the orchestrator records it as a per-source error — exactly like the existing per-source error path. A missing flag never takes down local recall.
  - All three run every response through `PrivacyBoundary.assert_safe_for_remote`. PII is redacted; a row whose body contains a *secret* (private key, JWT, bearer, API key, password, SSH key) is dropped silently rather than passed through. A `WebFetchAdapter` that pulls a secret out of a page raises `WebAdapterError` instead of returning a partial result.
  - Timeouts: 3 s search / 8 s fetch / 1 retry by default. Configurable via `policy.retrieval.web_search_timeout_s`, `web_fetch_timeout_s`, `web_retries`.
- **`DecisionAction.EXPLORE = "explore"`** — a new action meaning "do a ReAct-style loop: web_search → inspect → maybe web_fetch → answer". Hosts that do not implement ReAct should map `EXPLORE → RETRIEVE_THEN_ACT`. The action is forward-compatible.
- **EXPLORE branch in `PolicyEngine`** (v0.7.0+). Fires *before* the B/C v3 branches, so when remote is allowed and the host has not yet searched, "go to the web" supersedes local `RETRIEVE` / `RETRIEVE_THEN_ACT`. Two gates keep it tight:
  1. `remote_allowed` is set on **both** the config and the caller's `DecisionContext`.
  2. `recent_actions[-5:]` shows no `web_search` entry.
- **Schema** (`schemas/decision.schema.json`) adds `explore` to the action enum.
- **Golden tests** gain four EXPLORE fixtures: forced on complexity with remote, forced on uncertainty with remote, suppressed by recent `web_search`, downgraded to `retrieve_then_act` when remote flag is missing.
- **Unit tests** (`tests/unit/test_web_adapters.py`, `tests/unit/test_explore_branch.py`) cover the adapters with monkey-patched HTTP (no real network in CI) and the EXPLORE branch's gating conditions.

### Changed
- The v3 branch order is now A → D → B → C: pressure beats the web beats self-doubt beats planning. EXPLORE sits between pressure and the local retrieve branches. A v3-enabled engine without remote still hits B and C unchanged; an engine with remote that has not yet searched hits EXPLORE first.
- `CompassConfig` gains three new fields (`web_search_timeout_s`, `web_fetch_timeout_s`, `web_retries`). They are only consulted by the new adapters — the core policy engine never reads them.

### Still honest about
- EXPLORE is **opt-in via v3** (`policy_v3_enabled=True`). v2 is the default. A v2 engine never emits EXPLORE.
- The DDG HTML endpoint is unofficial. If DDG returns a captcha / 202 page the parser will see no results and the adapter raises `WebAdapterError`. That is recorded as a per-source failure by the orchestrator; the host falls back to local memory or to the Tavily backend.
- The bundled privacy detector is a **baseline**, not a complete DLP product. A page whose body contains something the detector does not recognise is summarised as-is.
- The summariser is still extractive (the same one used by the local store). It picks sentences; it does not understand them.
- A v0.7.0 adapter without `remote_allowed` does nothing. The flag is intentionally a global opt-in.
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.6.0 (2026-08-15)

### Added
- **policy-v3, opt-in** (`agent_compass.policy.engine`) — three new "action bias" branches that fire *before* the v2 ASK_USER / RETRIEVE / ANSWER_DIRECTLY order. Designed against the two symptoms a host agent surfaced on 2026-08-15: long thinking turns with no external action, and never reaching for a web search unprompted.
  - **`action_pressure`** — `consecutive_answer_directly ≥ action_pressure_threshold` (default 3) → `RETRIEVE_THEN_ACT`. Breaks the silent-thinking loop.
  - **`uncertainty_threshold`** — `uncertainty_score ≥ uncertainty_threshold` (default 0.5) → `RETRIEVE`. The host's own self-report overrides the legacy "I have enough context" branch.
  - **`complexity_without_recent_retrieval`** — `complexity_score ≥ complexity_threshold` (default 0.6) AND no retrieve-shaped action in `recent_actions[-5:]` → `RETRIEVE_THEN_ACT`. Multi-step work should not be answered from the first scratchpad; a host that has already gathered is left alone.
- **`DecisionAction.RETRIEVE_THEN_ACT`** — new action meaning "retrieve, then take at least one tool step before answering". Hosts that do not yet honour it can map to `RETRIEVE`; the action is forward-compatible.
- **`DecisionContext` gains four fields** (all defaulted to neutral so legacy hosts keep working): `complexity_score`, `uncertainty_score`, `consecutive_answer_directly`, `recent_actions`.
- **`CompassConfig`** gains the v3 gate and three thresholds: `policy_v3_enabled`, `complexity_threshold`, `uncertainty_threshold`, `action_pressure_threshold`. Env var `AGENT_COMPASS_POLICY_V3=true` flips the gate.
- **`agent-compass decide`** gains `--complexity-score`, `--uncertainty-score`, `--consecutive-answer-directly`, `--recent-action`.
- **Schema** (`schemas/decision.schema.json`) adds `retrieve_then_act` to the action enum.
- **Golden tests** (`tests/golden/decisions.json`) gain five v3 fixtures and the runner learns to flip the env var per fixture.
- **Docs** (`docs/behavior-policy.md`) gain a full v3 section with the rule rationale and three opt-in recipes. `docs/ROADMAP.md` is restructured: v0.6.0 becomes action-bias, v0.7.0 becomes the built-in web adapter.

### Changed
- The default `Decision.policy_version` stays `policy-v2`. The engine bumps it to `policy-v3` only when it actually consulted a v3 branch. This is the migration contract: every adopter gets to flip the gate independently of their context wiring.

### Still honest about
- v3 is **opt-in**, not default. v2 has a real semantic ("wait until the inputs are bad enough") that is the right default for a host that already knows its job. v3 is rescue mode.
- A v3-enabled engine with all new fields at their neutral defaults behaves identically to v2 and reports `policy-v2`. There is no silent upgrade.
- The three thresholds are informed defaults, not measured constants. They are exposed as plain config keys precisely so you can disagree with them.
- `_retrieved_recently()` is a substring heuristic on `recent_actions[-5:]`. A host that knows its own actions is encouraged to set `recent_actions` with the exact names it uses.
- v0.6.0 does **not** ship a built-in web adapter. `RETRIEVE_THEN_ACT` is still wired to local memory only. That is the v0.7.0 work.
- The privacy detector remains a **baseline**, not a complete DLP product.
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.5.0 (2026-08-15)

### Added
- **`agent_compass.context` module** — the small "current task" pointer a hook needs.
  - **`set_last_task_id(task_id)` / `get_last_task_id()` / `clear_last_task_id()`** — read and write `~/.claude/state/last_task_id`. The state directory is overridable via `AGENT_COMPASS_CLAUDE_STATE_DIR` and lives *outside* `data_dir` so a host that wipes its memory store does not lose the pointer.
  - **`resolve_task_id(explicit=..., unspecified=...)`** — the documented priority chain: explicit arg wins; `unspecified=True` then tries the state file, the `AGENT_COMPASS_TASK_ID` env var, then falls back to the literal string `"unspecified"`. Returns a `ContextResolution` with the source so a hook can audit its own bookkeeping.
- **`agent-compass context set/show/clear`** subcommand — for the `UserPromptSubmit` hook to write the pointer and for an operator (or a debug session) to inspect or reset it.
- **`task checkpoint --unspecified`** — the flag a `Stop` hook uses when it has no explicit task id. Resolves via the state file written by `UserPromptSubmit`, the env var, or the literal `"unspecified"` fallback. A literal "unspecified" return logs a warning to stderr (so a host can surface it) and creates a placeholder task on the fly so the checkpoint still lands. The `task_id_source` field on the response tells the host which branch fired.
- **`TaskService.checkpoint_or_create(task_id, phase, ...)`** — the service-level primitive the CLI uses. Returns `(task_dict, created)` so callers can tell whether a new placeholder task was born. The created task gets a fresh `task_xxxxx` id from the store; the literal `"unspecified"` id never lands in `tasks.task_id`.
- **Async `feedback add` by default** (`agent_compass.feedback.pending`).
  - **`append_pending(event)`** — write one line to `~/.claude/state/feedback_pending.jsonl`. Returns the path that was written.
  - **`swap_pending()`** — atomically read the pending file and truncate it, with a sibling `feedback_pending.lock` so two concurrent flushes do not race.
  - **`is_sync_mode()`** — reads `AGENT_COMPASS_FEEDBACK_SYNC=1` so a caller can opt back into synchronous writes from a cron or a unit test.
  - **`agent-compass feedback flush`** — read the pending file, persist each event through `FeedbackService.record`, return `{"flushed": N, "errors": [...], "considered": N}`.
  - **`agent-compass feedback add --sync`** — explicit per-call opt-out of async, for callers that prefer the immediate path.
  - The async path is exactly the missing piece 幻梦 asked for: a `PostToolUse` hook that also runs a sound reminder no longer races the SQLite write.
- **`AGENT_COMPASS_FEEDBACK_SYNC=1`** env var — flip `feedback add` back to synchronous mode for the entire host.
- **CHANGELOG placeholder removed** — the v0.5.0 row is no longer a target date; it is shipped.

### Changed
- `task checkpoint <task_id>` accepts an *optional* positional `task_id` (was required). When omitted, the CLI requires `--unspecified` and resolves via the chain.
- `feedback add` no longer writes to SQLite by default. The new `feedback flush` subcommand persists. Existing callers that rely on the synchronous path must use `--sync` or set `AGENT_COMPASS_FEEDBACK_SYNC=1`. The `tests/integration/test_cli.py::test_feedback_stats` regression was updated to use `--sync`; the test is about stats, not the async flow.

### Still honest about
- The `last_task_id` file is plain text. A host that races two `UserPromptSubmit` events in the same shell can see a torn read; in practice the `Stop` hook runs after the prompt and reads whatever the last `set` wrote.
- The async feedback path is best-effort. If the process dies between `append_pending` and `flush`, the queued events survive on disk and the next `feedback flush` picks them up.
- A `task checkpoint --unspecified` that lands on the literal `"unspecified"` id creates a placeholder task. The created task gets a fresh `task_xxxxx` id, so the placeholder does not pollute the literal `"unspecified"` namespace. A `Stop` hook should treat that as "I should have known which task to checkpoint" and surface the warning.
- The privacy detector is still a **baseline**, not a complete DLP product.
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

---

## 0.4.0 (2026-08-10)

### Added
- **`activation-v2` scoring** (`agent_compass.memory.scoring`) — an opt-in five-dimension formula: `base + (context + importance + emotion + instinct) / 4`. The four auxiliary dimensions are normalised to `[0, 1]` and averaged so none can outweigh base activation; `base` keeps its native ACT-R magnitude.
  - **`emotion_tag`** — affect attached to a memory, saturating at 1.0.
  - **`instinct_tag`** — a five-class minimal drive set (`survival` / `kin` / `resource` / `novelty` / `transmit`), chosen as the smallest set already present in single-celled organisms rather than a taxonomy of higher motivations.
  - **`retention_dual()`** — two-segment retention, steep for the first 7 days and flat afterwards. A single exponential cannot express "this memory was already reconsolidated, stop decaying it".
  - **`route_score()`** — dispatches on a record's stored `formula_version`. Unknown or missing versions fall back to v1, so no existing row is ever silently rescored.
  - **`memory_type="event"`** (importance 0.75, stability 365 days) for timeline anchors, which are rarely accessed but never stop being true.
- **`agent_compass.retrieval` package** — bounded, summary-first recall. **Retrieval never returns full memory bodies**; it returns a ranked digest plus the `memory_id` needed to fetch a body on demand.
  - **`Retriever` protocol** — any backend (local SQLite, a wiki, a synced remote store) plugs in behind one interface.
  - **`LocalMemoryRetriever`** — reads the SQLite store, filters by type / status / age, summarises.
  - **`CallableRetriever`** — wraps any `(query) -> rows` callable. The core still ships no third-party client; it ships the field mapping, normalisation and cross-source scoring instead.
  - **`RetrievalOrchestrator`** — fans out, dedupes by `memory_id`, ranks, and applies Top-K then a token budget. A retriever that raises is recorded per source and skipped, never fatal: a flaky remote store must not take down local recall.
  - **`summarize()`** — extractive, dependency-free, query-aware. Selected sentences are re-emitted in original reading order so the digest still parses as prose.
  - **`render_digest()`** — formats a result for a prompt, including an explicit note of everything withheld.
- **`Compass.recall(query, **overrides)`** and **`Compass.retrieval`**.
- **`MemoryService.get(memory_id)`** — the expand half of the retrieval contract.
- **`docs/retrieval-orchestration.md`** — the reasoning behind the multiplicative boost, the ordering of the three bounds, and the no-silent-truncation rule.

### Changed
- `MemoryCandidate` gained `emotion_tag` and `instinct_tag` (both `None` on v1 records).
- `MemoryService.propose()` accepts `emotion_tag` / `instinct_tag` / `formula_version`, and opts a record into v2 automatically when a tag is supplied. An unsupported `formula_version` is rejected rather than silently accepted.
- `MemoryService.touch()`, `prune()` and `consolidate()` now rescore through `route_score` instead of hardcoding v1.
- `ScoreBreakdown` gained `emotion` and `instinct` fields, defaulting to `0.0`.

### Fixed
- A walrus-operator typo in `tests/property/test_invariants.py` that made the entire property suite fail to collect.
- A property assertion that compared an un-rounded importance against the 6-decimal value `scoring.py` actually returns.

### Still honest about
- `activation-v1` remains the default. Nothing migrates on upgrade; v2 is per-record and opt-in.
- The token estimate is `len(text) / 2`, not a real tokenizer. It is a deliberately conservative monotonic proxy — over-estimating costs a slightly under-filled prompt, under-estimating costs a blown context window.
- The summariser is extractive. It selects sentences; it does not understand them.
- The emotion and instinct weights are informed guesses that are internally consistent, not measured constants. They are exposed as plain dicts precisely so you can disagree with them.
- The privacy detector is still a **baseline**, not a complete DLP product.
- Agent Compass still does not provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.

## 0.3.0 (2026-08-06)

### Added
- **`--format json|text`** and **`--no-color`** are now global flags on every human-facing subcommand. Default output is human-readable text with optional ANSI color; `--format json` keeps the previous machine-friendly behavior. The `serve` command always emits JSONL regardless of the flag.
- **New `text` formatter** (`agent_compass.formatters.TextFormatter`) renders decisions, tasks, memories, feedback, privacy scans, and the doctor report as multi-line text with semantic colors.
- **Zero-dependency ANSI helper** (`agent_compass.console`) supports basic 8-color output, Windows VT detection, and respects the `NO_COLOR` / `AGENT_COMPASS_NO_COLOR` / `AGENT_COMPASS_FORCE_COLOR` env vars.
- **`agent-compass repl`** — interactive shell powered only by the Python standard library. Commands: `decide`, `task create/show/list/advance/checkpoint/resume/delete`, `memory list/search/propose`, `privacy scan`, `feedback add/list/stats`, `doctor`, `help`, `exit`.
- **`memory search`** — substring search across content and keywords, with optional `--type`, `--status`, `--privacy`, `--min-score`, and `--limit` filters; results are sorted by score.
- **`task delete <id> [--soft]`** — hard delete by default; `--soft` transitions the task to a new `ARCHIVED` status that survives restart but is hidden from `task list` by default (use `--include-archived` to see it).
- **`feedback stats [--task-id]`** — counts by label (`positive` / `negative` / `neutral`) and scope.
- **JSONL protocol** gained `memory.search`, `task.delete`, and `feedback.stats` request types.

### Changed
- `TaskStatus` now has an `ARCHIVED` value; the state machine accepts `* → ARCHIVED` from any non-terminal state, and `ARCHIVED` is terminal.
- `SQLiteStore.list_tasks(include_archived=False)` defaults to hiding archived tasks.

### Still honest about
- The privacy detector is a **baseline**, not a complete DLP product.
- Agent Compass does **not** provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.
- We have not yet shipped a model-assisted adapter; `LLMAdapter` Protocol remains for downstream projects.
- The REPL keeps no command history file yet; run the same commands in another shell for now.

## 0.2.0 (2026-08-06)

### Added
- **Task persistence**: tasks, checkpoints, and idempotency keys now survive process restarts in SQLite.
- **Full memory lifecycle**: `candidate → accepted → active → stale → archived/deleted` with score-based pruning.
- **Configurable policy engine**: YAML/JSON config files with `destructive_actions`, `time_sensitive_keywords`, `ambiguity_threshold`, `max_retries`.
- **Auto-detection**: the policy engine inspects `user_input` and `proposed_actions` for time-sensitive and destructive markers when callers omit the boolean flags.
- **New decision actions** are now reachable: `STOP` (retry budget exhausted) and `CONSOLIDATE_MEMORY` (session ending/ended).
- **`CONTINUE` vs `RESUME`**: a clean split between staying in-session (`CONTINUE`) and restoring from a checkpoint after interruption (`RESUME`).
- **JSONL protocol expansion**: `task.create/advance/checkpoint/resume/list`, `memory.propose/list/touch/archive/delete/prune`, `privacy.scan`, `feedback.record/list`, `idempotency.commit`, and `doctor`. `agent-compass serve` is the new entry point.
- **`agent-compass validate <schema> <file>`**: validate any JSON document against the bundled schemas (`decision`, `task`, `memory`, `feedback`). Requires `jsonschema` (already a dev dependency).
- **`adapters` package**: `LLMAdapter` Protocol plus an offline `NullAdapter`. The core never imports a model SDK.
- **Schema validation tests** confirm that what the library emits matches `schemas/*.json`.
- **Golden tests** (`tests/golden/`) pin deterministic policy output for regressions.
- **Subprocess CLI tests** cover `doctor`, `decide`, `task`, `memory`, `privacy scan`, and `validate`.
- **Example hook set** (`hooks/settings.example.json`) wires up SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/Stop to the CLI and JSONL protocol.

### Changed
- `policy_version` is now `policy-v2`.
- `CompassConfig.load(path)` is the canonical constructor and reads YAML when PyYAML is available (the bundled YAML files use a small dependency-free subset parser as a fallback).
- `IdempotencyRegistry(store=...)` now persists committed keys when a store is provided.
- Decision `DecisionContext` gained fields: `proposed_actions`, `retry_count`, `retry_budget`, `session_state`, `interrupted`, `failure_streak`, `last_error`.
- `MemoryCandidate` gained: `status`, `access_count`, `last_accessed`, `related_task_id`, `formula_version`, `score`, plus lifecycle transitions.
- `SQLiteStore` adds `memories`, `checkpoints`, `idempotency_keys`, and a `meta` table for `schema_version`.

### Still honest about
- The privacy detector is a **baseline**, not a complete DLP product. Domain-specific secrets need domain-specific detectors.
- Agent Compass does **not** provide consciousness, subjective experience, true autonomous life, or permission to skip human approval for high-impact actions.
- We have not yet built a model-assisted classification adapter; the Protocol exists for downstream projects to plug in.
- The `privacy.classifier` module is currently a thin re-export; richer PII / secret heuristics will arrive in a later release.

## 0.1.0 (2026-07-31)

- Initial MVP: deterministic policy engine, in-memory state machine, baseline privacy boundary, memory scoring formula `activation-v1`, and a minimal SQLite store.
