"""Interactive REPL for Agent Compass.

The REPL is intentionally minimal: it uses only the Python standard library so
it works in restricted environments and on Windows. Available commands:

    decide <text>          Run the policy engine on a one-liner.
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
        ns = parser.parse_args(tokens)
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
        return "\n".join(
            [
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
                "  help                   this help",
                "  exit | quit            leave the REPL",
            ]
        )

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
