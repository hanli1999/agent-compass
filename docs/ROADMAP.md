# Agent Compass Roadmap

## Shipped

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

## v0.6.0 — Make agents smarter, not just safer

Target: 2026-09. Bigger ideas.

### Shared method pack

幻梦 and 银月 are now both running `agent-compass`. The natural next step is to ship a `.skills/yinyue-methods/` (and later `huanmeng-methods/`) pack that lets any adopter adopt proven retrieval / scoring / token-saving techniques without re-deriving them. v0.6.0 will:

- Document a `skill_pack` schema: a folder with `SKILL.md`, `README.md`, and optional code templates.
- Ship one example pack covering 双层检索 (index match → rerank → Top-7 detail), `activation-v2` scoring, and the token-saving rule set. Much of this landed as library code in v0.4.0; the pack becomes the *how to apply it* layer rather than a reimplementation.
- Add `agent-compass skill list / show / apply` so the CLI can read packs from a configurable directory.

### Cross-session synthesis

幻梦's day-one review surfaced a sharper question: can the host learn the user's *style* from accumulated `feedback.record` events? v0.6.0 experiments with:

- A local-only, deterministic feedback digest: per-task-id counts of `positive` / `negative` / `neutral`, exposed via `agent-compass feedback stats --json`.
- A `feedback trend` view that flags tasks where the negative ratio is climbing over consecutive checkpoints, so a `UserPromptSubmit` decision can downgrade its confidence automatically.

This is intentionally local and deterministic. No model, no remote. Just shape-of-traffic.

### Adopter loop

Once v0.5.0 ships and 幻梦 installs the remaining hooks, the README "First adopter" block becomes a template: every verified adopter appends a five-bullet report. v0.6.0 turns this into a small public ledger under `docs/adopters/` so the project has a real signal of who actually uses it.

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