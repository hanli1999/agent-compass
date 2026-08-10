# Changelog

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
