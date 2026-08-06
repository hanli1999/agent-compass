"""Command line interface and JSONL protocol entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import Compass
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
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor")
    sub.add_parser("serve", help="Read JSONL requests from stdin and write responses to stdout.")

    decide = sub.add_parser("decide")
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

    validate = sub.add_parser("validate", help="Validate a JSON document against a bundled schema.")
    validate.add_argument("schema", choices=["decision", "task", "memory", "feedback"])
    validate.add_argument("file", type=Path)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    create = task_sub.add_parser("create")
    create.add_argument("goal")
    show = task_sub.add_parser("show")
    show.add_argument("task_id")
    task_sub.add_parser("list")
    advance = task_sub.add_parser("advance")
    advance.add_argument("task_id")
    advance.add_argument("--target")
    advance.add_argument("--completed-step")
    advance.add_argument("--reason", default="")
    checkpoint = task_sub.add_parser("checkpoint")
    checkpoint.add_argument("task_id")
    checkpoint.add_argument("phase")
    checkpoint.add_argument("--completed-step", action="append", default=[])
    checkpoint.add_argument("--pending-step", action="append", default=[])
    checkpoint.add_argument("--note", action="append", default=[])
    checkpoint.add_argument("--artifact", action="append", default=[])
    resume = task_sub.add_parser("resume")
    resume.add_argument("task_id")

    privacy = sub.add_parser("privacy")
    privacy_sub = privacy.add_subparsers(dest="privacy_command", required=True)
    scan = privacy_sub.add_parser("scan")
    scan.add_argument("--input", help="Path to a UTF-8 text file to scan.")
    scan.add_argument("--text", help="Inline text to scan instead of --input path.")

    memory = sub.add_parser("memory")
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

    feedback = sub.add_parser("feedback")
    feedback_sub = feedback.add_subparsers(dest="feedback_command", required=True)
    add = feedback_sub.add_parser("add")
    add.add_argument("--signal", required=True)
    add.add_argument("--label", default="neutral")
    add.add_argument("--scope", default="this_task")
    add.add_argument("--task-id")
    add.add_argument("--decision-id")
    add.add_argument("--notes", default="")
    flist = feedback_sub.add_parser("list")
    flist.add_argument("--task-id")
    flist.add_argument("--limit", type=int, default=20)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "serve":
        return _serve()

    if args.command == "doctor":
        c = _compass_from_args(args)
        print(
            json.dumps(
                {
                    "ok": True,
                    "version": "0.2.0",
                    "policy_version": "policy-v2",
                    "data_dir": str(c.config.data_dir),
                    "schema_version": c.store.schema_version(),
                },
                ensure_ascii=False,
            )
        )
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
            )
        )
        print(json.dumps(d.to_dict(), ensure_ascii=False))
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
            print(json.dumps(c.tasks.create(args.goal).to_dict(), ensure_ascii=False))
            return 0
        if args.task_command == "show":
            value = c.tasks.get(args.task_id)
            if value is None:
                print(json.dumps({"error": "task_not_found", "task_id": args.task_id}, ensure_ascii=False))
                return 1
            print(json.dumps(value, ensure_ascii=False))
            return 0
        if args.task_command == "list":
            print(json.dumps({"tasks": c.tasks.list()}, ensure_ascii=False))
            return 0
        if args.task_command == "advance":
            print(
                json.dumps(
                    c.tasks.advance(
                        args.task_id,
                        target=args.target,
                        completed_step=args.completed_step,
                        reason=args.reason,
                    ),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.task_command == "checkpoint":
            print(
                json.dumps(
                    c.tasks.checkpoint(
                        args.task_id,
                        args.phase,
                        completed_steps=list(args.completed_step),
                        pending_steps=list(args.pending_step),
                        notes=list(args.note),
                        artifacts=list(args.artifact),
                    ),
                    ensure_ascii=False,
                )
            )
            return 0
        if args.task_command == "resume":
            print(json.dumps(c.tasks.resume(args.task_id), ensure_ascii=False))
            return 0

    if args.command == "privacy":
        if args.privacy_command == "scan":
            if args.text is None and not args.input:
                print(
                    json.dumps(
                        {"error": "missing_text", "message": "provide --text or --input"},
                        ensure_ascii=False,
                    )
                )
                return 2
            text = args.text if args.text is not None else Path(args.input).read_text(encoding="utf-8")
            result = PrivacyBoundary().inspect(text)
            print(
                json.dumps(
                    {
                        "level": result.level.name.lower(),
                        "matches": list(result.matches),
                        "blocked": result.blocked,
                        "redacted": PrivacyBoundary().redact(text) if result.matches else text,
                    },
                    ensure_ascii=False,
                )
            )
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
            print(json.dumps(memory.to_dict(), ensure_ascii=False))
            return 0
        if args.memory_command == "list":
            items = c.memory.list(status=args.status, privacy=args.privacy, limit=args.limit)
            print(json.dumps({"memories": items}, ensure_ascii=False))
            return 0
        if args.memory_command == "touch":
            print(json.dumps(c.memory.touch(args.memory_id).to_dict(), ensure_ascii=False))
            return 0
        if args.memory_command == "archive":
            print(json.dumps(c.memory.archive(args.memory_id).to_dict(), ensure_ascii=False))
            return 0
        if args.memory_command == "delete":
            print(json.dumps({"deleted": c.memory.delete(args.memory_id), "memory_id": args.memory_id}, ensure_ascii=False))
            return 0
        if args.memory_command == "prune":
            print(
                json.dumps(
                    c.memory.prune(below=args.below, stale_below=args.stale_below, dry_run=args.dry_run),
                    ensure_ascii=False,
                )
            )
            return 0

    if args.command == "feedback":
        c = _compass_from_args(args)
        if args.feedback_command == "add":
            event = c.feedback.record(
                args.signal,
                label=args.label,
                scope=args.scope,
                task_id=args.task_id,
                decision_id=args.decision_id,
                notes=args.notes,
            )
            print(json.dumps(event.to_dict(), ensure_ascii=False))
            return 0
        if args.feedback_command == "list":
            print(json.dumps({"feedback": c.feedback.list(task_id=args.task_id, limit=args.limit)}, ensure_ascii=False))
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
