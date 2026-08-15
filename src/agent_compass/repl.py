"""Interactive REPL for Agent Compass.

The REPL is intentionally minimal: it uses only the Python standard library so
it works in restricted environments and on Windows. Available commands:

    decide <text>          Run the policy engine on a one-liner.
    state                  (v3 only) Show the AutoTracker snapshot.
    record <name>          (v3 only) Record an action (or "answer") to the tracker.
    set_complexity <0..1>  (v3 only) Set the host-reported complexity.
    set_uncertainty <0..1> (v3 only) Set the host-reported uncertainty.
    reset_tracker          (v3 only) Reset the tracker to neutral.
    task create <goal>     Create a new task.
    task show <id>         Show a task.
    task list              List recent tasks.
    task advance <id> ...  Advance a task.
    task checkpoint <id> <phase>
    task resume <id>
    task delete <id> [--soft]
    memory list            List recent memories.
    memory search <query>  Search memories.
    memory propose <text>  Propose a new memory.
    privacy scan <text>    Scan a one-liner.
    feedback add --signal ok [--label positive] [--task-id t]
    feedback list
    feedback stats
    doctor                 Show the doctor report.
    help                   List commands.
    exit / quit            Leave the REPL.

The REPL auto-wires a :class:`HostLoop` when ``compass.config.policy_v3_enabled``
is True, so the v3 commands (``state`` / ``record`` / ``set-complexity`` /
``set-uncertainty`` / ``reset-tracker``) only work in that mode. To enable
v3, set ``AGENT_COMPASS_POLICY_V3=true`` or call ``apply_smart_defaults`` on
the underlying compass before launching the REPL. A v2 REPL session skips
the v3 commands entirely.

Unknown commands print a one-line hint instead of an argparse traceback so the
REPL stays friendly.
"""
from __future__ import annotations

import argparse
import shlex
from typing import Any

from . import __version__
from . import Compass
from .formatters import TextFormatter, make_formatter
from .models import DecisionContext, SessionState
from .privacy.boundary import PrivacyBoundary


_BANNER = """\
agent-compass {version} (policy {policy})
type 'help' for commands, 'exit' to quit.
"""


class CompassRepl:
    def __init__(self, compass: Compass, *, format_name: str = "text", color: bool = True):
        self.compass = compass
        self.fmt = make_formatter(format_name, color=color)
        self._history: list[str] = []
        # v3 wiring is opt-in via the compass config. If policy_v3_enabled
        # is on, the REPL auto-constructs a HostLoop so the v3 commands
        # (state / record / set-complexity / set-uncertainty / reset-tracker)
        # work. A v2 REPL session skips the wiring entirely and the v3
        # commands return a friendly 'v3 not enabled' message.
        self._loop = None
        if compass.config.policy_v3_enabled:
            from .runtime import HostLoop  # local import to keep v2 REPL light
            self._loop = HostLoop(compass)

    # ---------- dispatch ----------

    def run(self, line: str) -> str | None:
        line = line.strip()
        if not line:
            return ""
        self._history.append(line)
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            return f"parse error: {exc}"
        head, tail = tokens[0], tokens[1:]
        handler = getattr(self, f"do_{head}", None)
        if handler is None:
            return f"unknown command: {head!r}. type 'help' for the list."
        try:
            return handler(tail) or ""
        except KeyError as exc:
            return f"not found: {exc}"
        except ValueError as exc:
            return f"error: {exc}"

    # ---------- helpers ----------

    def _decide(self, text: str) -> str:
        tokens = shlex.split(text)
        parser = argparse.ArgumentParser(prog="decide", add_help=False)
        parser.add_argument("--input", required=True)
        parser.add_argument("--time-sensitive", action="store_true")
        parser.add_argument("--search", action="store_true")
        parser.add_argument("--remote", action="store_true")
        parser.add_argument("--context-sufficient", action="store_true")
        parser.add_argument("--interrupted", action="store_true")
        parser.add_argument("--retry-count", type=int, default=0)
        parser.add_argument("--ambiguous", type=float, default=0.0)
        parser.add_argument("--proposed-action", action="append", default=[])
        parser.add_argument(
            "--session-state",
            choices=["new", "ongoing", "interrupted", "ending", "ended"],
            default="new",
        )
        # v3 (v0.6.0+) per-call overrides. The values flow into the
        # DecisionContext unchanged; the host decides what to report.
        parser.add_argument("--complexity", type=float, default=None)
        parser.add_argument("--uncertainty", type=float, default=None)
        parser.add_argument("--consecutive-answer", type=int, default=None)
        parser.add_argument("--recent-action", action="append", default=[])
        ns = parser.parse_args(tokens)
        # When v3 is wired, route through the loop so the tracker's
        # snapshot (silent-answer counter, recent_actions) gets folded
        # into the DecisionContext. v2 sessions go straight to the
        # engine so the v2 test fixtures keep their unchanged semantics.
        if self._loop is not None:
            proposed = list(ns.proposed_action)
            # Pass remote_allowed only when the caller explicitly opted
            # in via --remote. The HostLoop auto-injects from the
            # compass config otherwise, which is what makes EXPLORE
            # fire on a complex task in a v3 REPL without the operator
            # having to remember the flag.
            kwargs = {
                "complexity": ns.complexity,
                "uncertainty": ns.uncertainty,
                "has_sufficient_context": ns.context_sufficient,
                "explicit_search_request": ns.search,
                "time_sensitive": ns.time_sensitive,
                "ambiguity": ns.ambiguous,
                "interrupted": ns.interrupted,
                "retry_count": ns.retry_count,
                "proposed_actions": proposed,
                "session_state": SessionState(ns.session_state),
            }
            if ns.remote:
                kwargs["remote_allowed"] = True
            decision = self._loop.decide(ns.input, **kwargs)
        else:
            decision = self.compass.decide(
                DecisionContext(
                    user_input=ns.input,
                    has_sufficient_context=ns.context_sufficient,
                    explicit_search_request=ns.search,
                    time_sensitive=ns.time_sensitive,
                    remote_allowed=ns.remote,
                    ambiguity=ns.ambiguous,
                    interrupted=ns.interrupted,
                    retry_count=ns.retry_count,
                    proposed_actions=list(ns.proposed_action),
                    session_state=SessionState(ns.session_state),
                )
            )
        return self.fmt.render_decision(decision.to_dict())

    def _memory_search(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="memory search", add_help=False)
        parser.add_argument("--query", default=None)
        parser.add_argument("--type", dest="memory_type", default=None)
        parser.add_argument("--status", default=None)
        parser.add_argument("--privacy", default=None)
        parser.add_argument("--min-score", type=float, default=None)
        parser.add_argument("--limit", type=int, default=20)
        ns = parser.parse_args(tokens)
        items = self.compass.memory.search(
            query=ns.query,
            memory_type=ns.memory_type,
            min_score=ns.min_score,
            status=ns.status,
            privacy=ns.privacy,
            limit=ns.limit,
        )
        return self.fmt.render_memories(items)

    def _memory_propose(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="memory propose", add_help=False)
        parser.add_argument("--content", default=None)
        parser.add_argument("--type", default="task_lesson")
        parser.add_argument("--privacy", default=None)
        parser.add_argument("--keyword", action="append", default=[])
        parser.add_argument("--related-task", default=None)
        ns = parser.parse_args(tokens)
        # Accept `memory propose <text>` as a shortcut.
        if ns.content is None and tokens and not tokens[0].startswith("-"):
            ns.content = " ".join(tokens)
        if not ns.content:
            raise ValueError("memory propose requires --content or a positional text")
        memory = self.compass.memory.propose(
            ns.content,
            memory_type=ns.type,
            privacy=ns.privacy,
            keywords=list(ns.keyword),
            related_task_id=ns.related_task,
        )
        return self.fmt.render_memory(memory.to_dict())

    def _memory_list(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="memory list", add_help=False)
        parser.add_argument("--status", default=None)
        parser.add_argument("--privacy", default=None)
        parser.add_argument("--limit", type=int, default=20)
        ns = parser.parse_args(tokens)
        return self.fmt.render_memories(
            self.compass.memory.list(status=ns.status, privacy=ns.privacy, limit=ns.limit)
        )

    def _memory_prune(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="memory prune", add_help=False)
        parser.add_argument("--below", type=float, default=0.15)
        parser.add_argument("--stale-below", type=float, default=0.3)
        parser.add_argument("--dry-run", action="store_true")
        ns = parser.parse_args(tokens)
        import json

        return json.dumps(
            self.compass.memory.prune(below=ns.below, stale_below=ns.stale_below, dry_run=ns.dry_run),
            ensure_ascii=False,
        )

    def _memory_consolidate(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="memory consolidate", add_help=False)
        parser.add_argument("--merge-threshold", type=float, default=0.5)
        parser.add_argument("--status", action="append", default=None)
        parser.add_argument("--dry-run", action="store_true")
        ns = parser.parse_args(tokens)
        import json

        return json.dumps(
            self.compass.memory.consolidate(
                merge_threshold=ns.merge_threshold,
                status_filter=list(ns.status) if ns.status else None,
                dry_run=ns.dry_run,
            ),
            ensure_ascii=False,
        )

    def _task_create(self, tokens: list[str]) -> str:
        # Goal can be multiple words; the REPL joins all remaining tokens so
        # `task create demo repl` produces goal="demo repl".
        if not tokens:
            raise ValueError("task create requires a goal")
        goal = " ".join(tokens)
        return self.fmt.render_task(self.compass.tasks.create(goal).to_dict())

    def _task_show(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="task show", add_help=False)
        parser.add_argument("task_id")
        ns = parser.parse_args(tokens)
        value = self.compass.tasks.get(ns.task_id)
        if value is None:
            raise KeyError(ns.task_id)
        return self.fmt.render_task(value)

    def _task_list(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="task list", add_help=False)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--include-archived", action="store_true")
        ns = parser.parse_args(tokens)
        return self.fmt.render_tasks(
            self.compass.tasks.list(limit=ns.limit, include_archived=ns.include_archived)
        )

    def _task_advance(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="task advance", add_help=False)
        parser.add_argument("task_id")
        parser.add_argument("--target", default=None)
        parser.add_argument("--completed-step", default=None)
        parser.add_argument("--reason", default="")
        ns = parser.parse_args(tokens)
        return self.fmt.render_task(
            self.compass.tasks.advance(
                ns.task_id,
                target=ns.target,
                completed_step=ns.completed_step,
                reason=ns.reason,
            )
        )

    def _task_checkpoint(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="task checkpoint", add_help=False)
        parser.add_argument("task_id")
        parser.add_argument("phase")
        parser.add_argument("--completed-step", action="append", default=[])
        parser.add_argument("--pending-step", action="append", default=[])
        parser.add_argument("--note", action="append", default=[])
        ns = parser.parse_args(tokens)
        return self.fmt.render_task(
            self.compass.tasks.checkpoint(
                ns.task_id,
                ns.phase,
                completed_steps=list(ns.completed_step),
                pending_steps=list(ns.pending_step),
                notes=list(ns.note),
            )
        )

    def _task_resume(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="task resume", add_help=False)
        parser.add_argument("task_id")
        ns = parser.parse_args(tokens)
        import json

        return json.dumps(self.compass.tasks.resume(ns.task_id), ensure_ascii=False)

    def _task_delete(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="task delete", add_help=False)
        parser.add_argument("task_id")
        parser.add_argument("--soft", action="store_true")
        ns = parser.parse_args(tokens)
        import json

        return json.dumps(self.compass.tasks.delete(ns.task_id, soft=ns.soft), ensure_ascii=False)

    def _privacy_scan(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="privacy scan", add_help=False)
        parser.add_argument("--text", required=True)
        ns = parser.parse_args(tokens)
        boundary = PrivacyBoundary()
        result = boundary.inspect(ns.text)
        payload = {
            "level": result.level.name.lower(),
            "matches": list(result.matches),
            "blocked": result.blocked,
            "redacted": boundary.redact(ns.text) if result.matches else ns.text,
        }
        return self.fmt.render_privacy(payload)

    def _feedback_add(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="feedback add", add_help=False)
        parser.add_argument("--signal", required=True)
        parser.add_argument("--label", default="neutral")
        parser.add_argument("--scope", default="this_task")
        parser.add_argument("--task-id", default=None)
        parser.add_argument("--notes", default="")
        ns = parser.parse_args(tokens)
        event = self.compass.feedback.record(
            ns.signal,
            label=ns.label,
            scope=ns.scope,
            task_id=ns.task_id,
            notes=ns.notes,
        )
        return self.fmt.render_feedback(event.to_dict())

    def _feedback_list(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="feedback list", add_help=False)
        parser.add_argument("--task-id", default=None)
        parser.add_argument("--limit", type=int, default=20)
        ns = parser.parse_args(tokens)
        return self.fmt.render_feedback_list(
            self.compass.feedback.list(task_id=ns.task_id, limit=ns.limit)
        )

    def _feedback_stats(self, tokens: list[str]) -> str:
        parser = argparse.ArgumentParser(prog="feedback stats", add_help=False)
        parser.add_argument("--task-id", default=None)
        ns = parser.parse_args(tokens)
        import json

        return json.dumps(self.compass.feedback.stats(task_id=ns.task_id), ensure_ascii=False)

    def _doctor(self, _tokens: list[str]) -> str:
        c = self.compass
        return self.fmt.render_doctor(
            {
                "ok": True,
                "version": __version__,
                "policy_version": "policy-v2",
                "data_dir": str(c.config.data_dir),
                "schema_version": c.store.schema_version(),
            }
        )

    def _help(self, _tokens: list[str]) -> str:
        lines = [
            "commands:",
            "  decide <text>          run the policy engine",
            "  task create <goal>     create a task",
            "  task show <id>         show a task",
            "  task list              list recent tasks",
            "  task advance <id> ...  advance a task",
            "  task checkpoint <id> <phase>",
            "  task resume <id>",
            "  task delete <id> [--soft]",
            "  memory list            list recent memories",
            "  memory search <query>  search memories",
            "  memory propose <text>  propose a memory",
            "  privacy scan <text>    scan a one-liner",
            "  feedback add --signal ok [--label positive] [--task-id t]",
            "  feedback list",
            "  feedback stats",
            "  doctor                 show doctor report",
        ]
        if self._loop is not None:
            lines.extend(
                [
                    "  state                  show the v3 tracker snapshot",
                    "  record <name|answer>   record a tool call or an answer",
                    "  set_complexity <0..1>  set the host-reported complexity",
                    "  set_uncertainty <0..1> set the host-reported uncertainty",
                    "  reset_tracker          reset the tracker to neutral",
                ]
            )
        lines.extend(
            [
                "  help                   this help",
                "  exit | quit            leave the REPL",
            ]
        )
        return "\n".join(lines)

    # ---------- v3 commands (only when policy_v3_enabled) ----------

    def _v3_required(self, command: str) -> str | None:
        """Return an error message if v3 is not wired; None when ready."""
        if self._loop is None:
            return (
                f"v3 not enabled: {command} requires CompassConfig.policy_v3_enabled=True. "
                "Set AGENT_COMPASS_POLICY_V3=true or call apply_smart_defaults(compass) "
                "before launching the REPL."
            )
        return None

    def _state(self, _tokens: list[str]) -> str:
        err = self._v3_required("state")
        if err:
            return err
        import json
        return json.dumps(self._loop.explain(), indent=2, ensure_ascii=False)

    def _record(self, tokens: list[str]) -> str:
        err = self._v3_required("record")
        if err:
            return err
        if not tokens:
            return "usage: record <tool-name> | record answer"
        kind = " ".join(tokens)  # tool names can be multi-word
        self._loop.record(kind)
        return f"recorded: {kind}"

    def _set_complexity(self, tokens: list[str]) -> str:
        err = self._v3_required("set_complexity")
        if err:
            return err
        if not tokens:
            return "usage: set_complexity <0..1>"
        try:
            value = float(tokens[0])
        except ValueError:
            return f"set_complexity requires a number in [0, 1], got {tokens[0]!r}"
        self._loop.tracker.set_complexity(value)
        return f"complexity set to {self._loop.tracker.complexity_score:.2f}"

    def _set_uncertainty(self, tokens: list[str]) -> str:
        err = self._v3_required("set_uncertainty")
        if err:
            return err
        if not tokens:
            return "usage: set_uncertainty <0..1>"
        try:
            value = float(tokens[0])
        except ValueError:
            return f"set_uncertainty requires a number in [0, 1], got {tokens[0]!r}"
        self._loop.tracker.set_uncertainty(value)
        return f"uncertainty set to {self._loop.tracker.uncertainty_score:.2f}"

    def _reset_tracker(self, _tokens: list[str]) -> str:
        err = self._v3_required("reset_tracker")
        if err:
            return err
        self._loop.tracker.reset()
        return "tracker reset to neutral"

    # ---------- command bindings ----------

    def do_decide(self, rest: list[str]):
        # `rest` already shlex-split from the REPL line. The shorthand
        # `decide <text>` is accepted as long as no flag-like token is present.
        if rest and all(not token.startswith("--") for token in rest):
            return self._decide("--input " + shlex.join(rest))
        return self._decide(shlex.join(rest))

    def do_task(self, rest: list[str]):
        if not rest:
            return "usage: task <create|show|list|advance|checkpoint|resume|delete>"
        sub, *args = rest
        return {
            "create": self._task_create,
            "show": self._task_show,
            "list": self._task_list,
            "advance": self._task_advance,
            "checkpoint": self._task_checkpoint,
            "resume": self._task_resume,
            "delete": self._task_delete,
        }.get(sub, lambda _a: f"unknown task subcommand: {sub!r}")(args)

    def do_memory(self, rest: list[str]):
        if not rest:
            return "usage: memory <list|search|propose|prune|consolidate>"
        sub, *args = rest
        return {
            "list": self._memory_list,
            "search": self._memory_search,
            "propose": self._memory_propose,
            "prune": self._memory_prune,
            "consolidate": self._memory_consolidate,
        }.get(sub, lambda _a: f"unknown memory subcommand: {sub!r}")(args)

    def do_privacy(self, rest: list[str]):
        if rest and rest[0] == "scan":
            return self._privacy_scan(rest[1:])
        return self._privacy_scan(rest)

    def do_feedback(self, rest: list[str]):
        if not rest:
            return "usage: feedback <add|list|stats>"
        sub, *args = rest
        return {
            "add": self._feedback_add,
            "list": self._feedback_list,
            "stats": self._feedback_stats,
        }.get(sub, lambda _a: f"unknown feedback subcommand: {sub!r}")(args)

    def do_doctor(self, _rest: list[str]):
        return self._doctor([])

    def do_state(self, rest: list[str]):
        return self._state(rest)

    def do_record(self, rest: list[str]):
        return self._record(rest)

    def do_set_complexity(self, rest: list[str]):
        return self._set_complexity(rest)

    def do_set_uncertainty(self, rest: list[str]):
        return self._set_uncertainty(rest)

    def do_reset_tracker(self, rest: list[str]):
        return self._reset_tracker(rest)

    def do_help(self, _rest: list[str]):
        return self._help([])

    def do_exit(self, _rest: list[str]):
        raise SystemExit(0)

    def do_quit(self, _rest: list[str]):
        raise SystemExit(0)


def _loop(repl: CompassRepl, stdin, stdout) -> int:
    stdout.write(_BANNER.format(version=__version__, policy="policy-v2"))
    stdout.flush()
    for line in stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            result = repl.run(line)
        except SystemExit:
            stdout.write("bye.\n")
            return 0
        if result:
            stdout.write(result + "\n")
        stdout.flush()
    return 0


def run_repl(args: argparse.Namespace) -> int:
    import os
    import sys

    compass = Compass.from_config(getattr(args, "config", None))
    # The global parser exposes --no-color and --format, but when REPL is launched
    # directly via `python -m agent_compass.cli repl` the global flags may be
    # stripped by argparse if they were given after the subcommand. Fall back to
    # environment variables so the REPL stays honest about colors.
    color_env = os.environ.get("NO_COLOR") is None and os.environ.get("AGENT_COMPASS_NO_COLOR") is None
    color = color_env and not getattr(args, "no_color", False)
    format_name = getattr(args, "format", None) or os.environ.get("AGENT_COMPASS_FORMAT", "text")
    repl = CompassRepl(compass, format_name=format_name, color=color)
    return _loop(repl, sys.stdin, sys.stdout)
