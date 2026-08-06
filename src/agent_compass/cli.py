"""Command line interface and JSONL protocol."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import Compass
from .models import DecisionContext, TaskStatus
from .privacy.boundary import PrivacyBoundary
from .memory.scoring import score_memory


def _compass() -> Compass:
    return Compass.from_config()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-compass", description="Local-first behavior and task state layer")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor")
    decide = sub.add_parser("decide")
    decide.add_argument("--input", required=True)
    decide.add_argument("--context-sufficient", action="store_true")
    decide.add_argument("--search", action="store_true")
    decide.add_argument("--time-sensitive", action="store_true")
    decide.add_argument("--remote", action="store_true")
    decide.add_argument("--ambiguous", type=float, default=0.0)
    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command")
    create = task_sub.add_parser("create")
    create.add_argument("goal")
    show = task_sub.add_parser("show")
    show.add_argument("task_id")
    privacy = sub.add_parser("privacy")
    privacy_sub = privacy.add_subparsers(dest="privacy_command")
    scan = privacy_sub.add_parser("scan")
    scan.add_argument("--input", required=True)
    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_command")
    score = memory_sub.add_parser("score")
    score.add_argument("--access-count", type=int, default=0)
    score.add_argument("--days", type=float, default=0)
    score.add_argument("--keywords", type=int, default=0)
    score.add_argument("--type", default="task_lesson")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        c = _compass()
        print(json.dumps({"ok": True, "version": "0.1.0", "data_dir": str(c.config.data_dir)}, ensure_ascii=False))
        return 0

    if args.command == "decide":
        c = _compass()
        d = c.decide(DecisionContext(user_input=args.input, has_sufficient_context=args.context_sufficient, explicit_search_request=args.search, time_sensitive=args.time_sensitive, remote_allowed=args.remote, ambiguity=args.ambiguous))
        print(json.dumps(d.to_dict(), ensure_ascii=False))
        return 0

    if args.command == "task" and args.task_command == "create":
        task_obj = _compass().tasks.create(args.goal)
        print(json.dumps(task_obj.to_dict(), ensure_ascii=False))
        return 0

    if args.command == "task" and args.task_command == "show":
        value = _compass().tasks.get(args.task_id)
        if value is None:
            print(json.dumps({"error": "task_not_found", "task_id": args.task_id}, ensure_ascii=False))
            return 1
        print(json.dumps(value, ensure_ascii=False))
        return 0

    if args.command == "privacy" and args.privacy_command == "scan":
        text = Path(args.input).read_text(encoding="utf-8")
        result = PrivacyBoundary().inspect(text)
        print(json.dumps({"level": result.level.name.lower(), "matches": result.matches, "blocked": result.blocked}, ensure_ascii=False))
        return 2 if result.blocked else (1 if result.matches else 0)

    if args.command == "memory" and args.memory_command == "score":
        result = score_memory(access_count=args.access_count, days_elapsed=args.days, keyword_hits=args.keywords, memory_type=args.type)
        print(json.dumps(result.__dict__, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


def jsonl_loop() -> int:
    """Process provider-neutral JSONL requests from stdin."""
    compass = _compass()
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        kind = request.get("type")
        payload = request.get("payload", {})
        if kind == "decision.request":
            context = DecisionContext(**payload)
            response = {"type": "decision.response", "request_id": request.get("request_id"), "payload": compass.decide(context).to_dict()}
        else:
            response = {"type": "error", "request_id": request.get("request_id"), "payload": {"code": "unsupported_request"}}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
