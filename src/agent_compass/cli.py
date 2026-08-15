"""Command line interface and JSONL protocol entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import Compass
from .formatters import make_formatter
from .models import DecisionContext
from .privacy.boundary import PrivacyBoundary
from .memory.scoring import score_memory
from .protocol import handle, serve as _serve


def _compass_from_args(args: argparse.Namespace) -> Compass:
    return Compass.from_config(args.config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-compass",
        description="Local-first behavior and task state layer",
    )
    parser.add_argument(
        "--config",
        help="Path to a YAML or JSON config file (overrides AGENT_COMPASS_CONFIG).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format for human-facing subcommands. Default: text.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color in text output (also honors the NO_COLOR env var).",
    )
    sub = parser.add_subparsers(dest="command")

    def _attach_common(p: argparse.ArgumentParser) -> None:
        # Re-declare the global flags on every subparser so callers can put them
        # after the subcommand (e.g. `agent-compass repl --no-color`).
        p.add_argument("--config", help=argparse.SUPPRESS)
        p.add_argument("--format", choices=["json", "text"], default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        p.add_argument("--no-color", action="store_true", help=argparse.SUPPRESS)

    sub.add_parser("doctor")
    serve = sub.add_parser("serve", help="Read JSONL requests from stdin and write responses to stdout.")
    _attach_common(serve)
    repl = sub.add_parser("repl", help="Start an interactive shell.")
    _attach_common(repl)

    decide = sub.add_parser("decide")
    _attach_common(decide)
    decide.add_argument("--input", required=True)
    decide.add_argument("--context-sufficient", action="store_true")
    decide.add_argument("--search", action="store_true")
    decide.add_argument("--time-sensitive", action="store_true")
    decide.add_argument("--remote", action="store_true")
    decide.add_argument("--ambiguous", type=float, default=0.0)
    decide.add_argument("--interrupted", action="store_true")
    decide.add_argument("--retry-count", type=int, default=0)
    decide.add_argument("--proposed-action", action="append", default=[])
    decide.add_argument(
        "--session-state",
        choices=["new", "ongoing", "interrupted", "ending", "ended"],
        default="new",
    )
    # policy-v3 (0.6.0+) — opt-in. The CLI passes them through; the engine only
    # consults them when CompassConfig.policy_v3_enabled is True.
    decide.add_argument("--complexity-score", type=float, default=0.0,
                        help="Host's read of task complexity in [0, 1]. v3 only.")
    decide.add_argument("--uncertainty-score", type=float, default=0.0,
                        help="Host's read of self-uncertainty in [0, 1]. v3 only.")
    decide.add_argument("--consecutive-answer-directly", type=int, default=0,
                        help="How many ANSWER_DIRECTLY decisions in a row. v3 only.")
    decide.add_argument("--recent-action", action="append", default=[],
                        help="Name of a recent tool action; may be repeated. v3 only.")

    validate = sub.add_parser("validate", help="Validate a JSON document against a bundled schema.")
    _attach_common(validate)
    validate.add_argument("schema", choices=["decision", "task", "memory", "feedback"])
    validate.add_argument("file", type=Path)

    task = sub.add_parser("task")
    _attach_common(task)
    task_sub = task.add_subparsers(dest="task_command", required=True)
    create = task_sub.add_parser("create")
    create.add_argument("goal")
    show = task_sub.add_parser("show")
    show.add_argument("task_id")
    list_t = task_sub.add_parser("list")
    list_t.add_argument("--limit", type=int, default=20)
    list_t.add_argument("--include-archived", action="store_true")
    advance = task_sub.add_parser("advance")
    advance.add_argument("task_id")
    advance.add_argument("--target")
    advance.add_argument("--completed-step")
    advance.add_argument("--reason", default="")
    checkpoint = task_sub.add_parser("checkpoint")
    checkpoint.add_argument("task_id", nargs="?", default=None,
                            help="Task id. Omit with --unspecified to resolve from the last_task_id state file, "
                                 "the AGENT_COMPASS_TASK_ID env var, or fall back to 'unspecified'.")
    checkpoint.add_argument("--unspecified", action="store_true",
                            help="Skip explicit task_id and resolve via state file -> env -> 'unspecified'.")
    checkpoint.add_argument("phase")
    checkpoint.add_argument("--completed-step", action="append", default=[])
    checkpoint.add_argument("--pending-step", action="append", default=[])
    checkpoint.add_argument("--note", action="append", default=[])
    checkpoint.add_argument("--artifact", action="append", default=[])
    resume = task_sub.add_parser("resume")
    resume.add_argument("task_id")
    delete_t = task_sub.add_parser("delete")
    delete_t.add_argument("task_id")
    delete_t.add_argument("--soft", action="store_true", help="Archive instead of hard delete.")

    privacy = sub.add_parser("privacy")
    _attach_common(privacy)
    privacy_sub = privacy.add_subparsers(dest="privacy_command", required=True)
    scan = privacy_sub.add_parser("scan")
    scan.add_argument("--input", help="Path to a UTF-8 text file to scan.")
    scan.add_argument("--text", help="Inline text to scan instead of --input path.")

    memory = sub.add_parser("memory")
    _attach_common(memory)
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    score = memory_sub.add_parser("score")
    score.add_argument("--access-count", type=int, default=0)
    score.add_argument("--days", type=float, default=0)
    score.add_argument("--keywords", type=int, default=0)
    score.add_argument("--type", default="task_lesson")
    score.add_argument("--importance", type=float, default=None)
    propose = memory_sub.add_parser("propose")
    propose.add_argument("--content", required=True)
    propose.add_argument("--type", default="task_lesson")
    propose.add_argument("--privacy", default=None)
    propose.add_argument("--keyword", action="append", default=[])
    propose.add_argument("--related-task")
    list_mem = memory_sub.add_parser("list")
    list_mem.add_argument("--status", default=None)
    list_mem.add_argument("--privacy", default=None)
    list_mem.add_argument("--limit", type=int, default=20)
    search = memory_sub.add_parser("search")
    search.add_argument("--query", default=None, help="Substring matched against content and keywords.")
    search.add_argument("--type", default=None, dest="memory_type", help="Filter by memory_type.")
    search.add_argument("--status", default=None)
    search.add_argument("--privacy", default=None)
    search.add_argument("--min-score", type=float, default=None)
    search.add_argument("--limit", type=int, default=20)
    touch = memory_sub.add_parser("touch")
    touch.add_argument("memory_id")
    archive = memory_sub.add_parser("archive")
    archive.add_argument("memory_id")
    delete = memory_sub.add_parser("delete")
    delete.add_argument("memory_id")
    prune = memory_sub.add_parser("prune")
    prune.add_argument("--below", type=float, default=0.15)
    prune.add_argument("--stale-below", type=float, default=0.3)
    prune.add_argument("--dry-run", action="store_true")
    consolidate = memory_sub.add_parser("consolidate")
    consolidate.add_argument("--merge-threshold", type=float, default=0.5)
    consolidate.add_argument("--status", action="append", default=None, help="Filter to memories in this status (repeatable).")
    consolidate.add_argument("--dry-run", action="store_true")

    feedback = sub.add_parser("feedback")
    _attach_common(feedback)
    feedback_sub = feedback.add_subparsers(dest="feedback_command", required=True)
    add = feedback_sub.add_parser("add")
    add.add_argument("--signal", required=True)
    add.add_argument("--label", default="neutral")
    add.add_argument("--scope", default="this_task")
    add.add_argument("--task-id")
    add.add_argument("--decision-id")
    add.add_argument("--notes", default="")
    add.add_argument("--sync", action="store_true",
                     help="Write to the SQLite store immediately instead of the async pending file. "
                          "Equivalent to setting AGENT_COMPASS_FEEDBACK_SYNC=1.")
    flush = feedback_sub.add_parser("flush",
                                    help="Persist all queued async feedback events. Idempotent.")
    flist = feedback_sub.add_parser("list")
    flist.add_argument("--task-id")
    flist.add_argument("--limit", type=int, default=20)
    stats = feedback_sub.add_parser("stats")
    stats.add_argument("--task-id")

    # v0.5.0+ — small "current task" pointer for hooks. UserPromptSubmit
    # writes it; Stop reads it. The state lives outside ``data_dir`` so
    # wiping the memory store does not lose the pointer.
    context = sub.add_parser("context",
                             help="Read or write the per-session 'current task' pointer used by hooks.")
    _attach_common(context)
    context_sub = context.add_subparsers(dest="context_command", required=True)
    context_set = context_sub.add_parser("set")
    context_set.add_argument("--task-id", required=True)
    context_show = context_sub.add_parser("show")
    context_clear = context_sub.add_parser("clear")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    color = not args.no_color
    fmt = make_formatter(args.format, color=color)

    if args.command == "serve":
        return _serve()

    if args.command == "repl":
        from .repl import run_repl

        return run_repl(args)

    if args.command == "doctor":
        c = _compass_from_args(args)
        payload = {
            "ok": True,
            "version": __version__,
            "policy_version": "policy-v2",
            "data_dir": str(c.config.data_dir),
            "schema_version": c.store.schema_version(),
        }
        print(fmt.render_doctor(payload))
        return 0

    if args.command == "decide":
        c = _compass_from_args(args)
        from .models import SessionState

        d = c.decide(
            DecisionContext(
                user_input=args.input,
                has_sufficient_context=args.context_sufficient,
                explicit_search_request=args.search,
                time_sensitive=args.time_sensitive,
                remote_allowed=args.remote,
                ambiguity=args.ambiguous,
                interrupted=args.interrupted,
                retry_count=args.retry_count,
                proposed_actions=list(args.proposed_action),
                session_state=SessionState(args.session_state),
                complexity_score=args.complexity_score,
                uncertainty_score=args.uncertainty_score,
                consecutive_answer_directly=args.consecutive_answer_directly,
                recent_actions=list(args.recent_action),
            )
        )
        print(fmt.render_decision(d.to_dict()))
        return 0

    if args.command == "validate":
        from .schemas import validate

        document = json.loads(args.file.read_text(encoding="utf-8"))
        ok, errors = validate(args.schema, document)
        print(json.dumps({"ok": ok, "errors": errors}, ensure_ascii=False))
        return 0 if ok else 1

    if args.command == "task":
        c = _compass_from_args(args)
        if args.task_command == "create":
            task_obj = c.tasks.create(args.goal)
            print(fmt.render_task(task_obj.to_dict()))
            return 0
        if args.task_command == "show":
            value = c.tasks.get(args.task_id)
            if value is None:
                print(json.dumps({"error": "task_not_found", "task_id": args.task_id}, ensure_ascii=False))
                return 1
            print(fmt.render_task(value))
            return 0
        if args.task_command == "list":
            tasks = c.tasks.list(limit=args.limit, include_archived=args.include_archived)
            print(fmt.render_tasks(tasks))
            return 0
        if args.task_command == "advance":
            updated = c.tasks.advance(
                args.task_id,
                target=args.target,
                completed_step=args.completed_step,
                reason=args.reason,
            )
            print(fmt.render_task(updated))
            return 0
        if args.task_command == "checkpoint":
            from .context import resolve_task_id
            resolution = resolve_task_id(explicit=args.task_id, unspecified=args.unspecified)
            if resolution.source == "missing":
                # The caller did not pass a task id, did not pass
                # --unspecified, and the resolution chain turned up
                # nothing. That is a real error — fail loudly so the
                # host's hook machinery surfaces it.
                print(
                    "error: task checkpoint needs a task_id, or --unspecified to resolve from "
                    "~/.claude/state/last_task_id / AGENT_COMPASS_TASK_ID / 'unspecified'",
                    file=sys.stderr,
                )
                return 2
            if resolution.source != "explicit":
                # Resolved via state file, env var, or the literal
                # "unspecified" fallback. Log a warning on stderr so a
                # Stop hook falling through to "unspecified" is
                # observable, and reflect the source in the JSON
                # payload so the host can audit its own bookkeeping.
                message = (
                    f"warning: task checkpoint resolved task_id={resolution.task_id!r} "
                    f"via {resolution.source!r}; pass an explicit task_id to silence this."
                )
                print(message, file=sys.stderr)
            updated, created = c.tasks.checkpoint_or_create(
                resolution.task_id,
                args.phase,
                completed_steps=list(args.completed_step),
                pending_steps=list(args.pending_step),
                notes=list(args.note),
                artifacts=list(args.artifact),
            )
            payload = updated
            if isinstance(payload, dict) and resolution.source != "explicit":
                payload = {**payload, "task_id_source": resolution.source, "task_created": created}
            print(fmt.render_task(payload))
            return 0
        if args.task_command == "resume":
            print(json.dumps(c.tasks.resume(args.task_id), ensure_ascii=False))
            return 0
        if args.task_command == "delete":
            try:
                result = c.tasks.delete(args.task_id, soft=args.soft)
            except KeyError:
                print(json.dumps({"error": "task_not_found", "task_id": args.task_id}, ensure_ascii=False))
                return 1
            print(json.dumps(result, ensure_ascii=False))
            return 0

    if args.command == "privacy":
        if args.privacy_command == "scan":
            if args.text is None and not args.input:
                print(json.dumps({"error": "missing_text", "message": "provide --text or --input"}, ensure_ascii=False))
                return 2
            text = args.text if args.text is not None else Path(args.input).read_text(encoding="utf-8")
            result = PrivacyBoundary().inspect(text)
            payload = {
                "level": result.level.name.lower(),
                "matches": list(result.matches),
                "blocked": result.blocked,
                "redacted": PrivacyBoundary().redact(text) if result.matches else text,
            }
            print(fmt.render_privacy(payload))
            return 2 if result.blocked else (1 if result.matches else 0)

    if args.command == "memory":
        c = _compass_from_args(args)
        if args.memory_command == "score":
            result = score_memory(
                access_count=args.access_count,
                days_elapsed=args.days,
                keyword_hits=args.keywords,
                memory_type=args.type,
                importance=args.importance,
            )
            if args.format == "text":
                print(fmt.render_kv("memory score", result.__dict__))
            else:
                print(json.dumps(result.__dict__, ensure_ascii=False))
            return 0
        if args.memory_command == "propose":
            memory = c.memory.propose(
                args.content,
                memory_type=args.type,
                privacy=args.privacy,
                keywords=list(args.keyword),
                related_task_id=args.related_task,
            )
            print(fmt.render_memory(memory.to_dict()))
            return 0
        if args.memory_command == "list":
            items = c.memory.list(status=args.status, privacy=args.privacy, limit=args.limit)
            print(fmt.render_memories(items))
            return 0
        if args.memory_command == "search":
            items = c.memory.search(
                query=args.query,
                memory_type=args.memory_type,
                min_score=args.min_score,
                status=args.status,
                privacy=args.privacy,
                limit=args.limit,
            )
            print(fmt.render_memories(items))
            return 0
        if args.memory_command == "touch":
            print(fmt.render_memory(c.memory.touch(args.memory_id).to_dict()))
            return 0
        if args.memory_command == "archive":
            print(fmt.render_memory(c.memory.archive(args.memory_id).to_dict()))
            return 0
        if args.memory_command == "delete":
            print(json.dumps({"deleted": c.memory.delete(args.memory_id), "memory_id": args.memory_id}, ensure_ascii=False))
            return 0
        if args.memory_command == "prune":
            print(json.dumps(c.memory.prune(below=args.below, stale_below=args.stale_below, dry_run=args.dry_run), ensure_ascii=False))
            return 0
        if args.memory_command == "consolidate":
            summary = c.memory.consolidate(
                merge_threshold=args.merge_threshold,
                status_filter=list(args.status) if args.status else None,
                dry_run=args.dry_run,
            )
            print(json.dumps(summary, ensure_ascii=False))
            return 0

    if args.command == "feedback":
        from .feedback.pending import append_pending, is_sync_mode

        c = _compass_from_args(args)
        if args.feedback_command == "add":
            event_payload = {
                "signal": args.signal,
                "label": args.label,
                "scope": args.scope,
                "task_id": args.task_id,
                "decision_id": args.decision_id,
                "notes": args.notes,
            }
            if args.sync or is_sync_mode():
                event = c.feedback.record(
                    args.signal,
                    label=args.label,
                    scope=args.scope,
                    task_id=args.task_id,
                    decision_id=args.decision_id,
                    notes=args.notes,
                )
                print(fmt.render_feedback(event.to_dict()))
                return 0
            # Default async path: append to the pending file and return
            # 0 immediately. ``feedback flush`` will persist it later.
            pending_path = append_pending(event_payload)
            print(json.dumps({
                "queued": True,
                "pending_file": str(pending_path),
                "payload": event_payload,
            }, ensure_ascii=False))
            return 0
        if args.feedback_command == "flush":
            summary = c.feedback.flush_pending()
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        if args.feedback_command == "list":
            print(fmt.render_feedback_list(c.feedback.list(task_id=args.task_id, limit=args.limit)))
            return 0
        if args.feedback_command == "stats":
            print(json.dumps(c.feedback.stats(task_id=args.task_id), ensure_ascii=False))
            return 0

    if args.command == "context":
        from .context import (
            clear_last_task_id,
            get_last_task_id,
            set_last_task_id,
        )

        if args.context_command == "set":
            path = set_last_task_id(args.task_id)
            print(json.dumps({"task_id": args.task_id, "path": str(path)}, ensure_ascii=False))
            return 0
        if args.context_command == "show":
            value = get_last_task_id()
            print(json.dumps({"task_id": value}, ensure_ascii=False))
            return 0 if value is not None else 1
        if args.context_command == "clear":
            cleared = clear_last_task_id()
            print(json.dumps({"cleared": cleared}, ensure_ascii=False))
            return 0

    parser.print_help()
    return 0


def jsonl_loop() -> int:
    """Backwards-compatible wrapper around the protocol dispatcher."""
    compass = Compass.from_config()
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        response = handle(compass, request)
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
