"""Output formatters for the Agent Compass CLI.

A formatter takes a model object (decision, task, memory, feedback, etc.) and
returns a rendered string. Two formatters are bundled: ``JsonFormatter`` for
machine consumption and ``TextFormatter`` for humans. The text formatter
honors the ``color`` flag so a downstream caller can disable coloring when
piping output to a file.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from . import console


class Formatter(Protocol):
    def render_decision(self, decision: dict) -> str: ...
    def render_task(self, task: dict) -> str: ...
    def render_tasks(self, tasks: list[dict]) -> str: ...
    def render_memory(self, memory: dict) -> str: ...
    def render_memories(self, memories: list[dict]) -> str: ...
    def render_feedback(self, event: dict) -> str: ...
    def render_feedback_list(self, events: list[dict]) -> str: ...
    def render_privacy(self, payload: dict) -> str: ...
    def render_doctor(self, payload: dict) -> str: ...
    def render_kv(self, title: str, payload: dict) -> str: ...


class JsonFormatter:
    """Default formatter. Stable, machine-readable, no decoration."""

    def _dump(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def render_decision(self, decision: dict) -> str:
        return self._dump(decision)

    def render_task(self, task: dict) -> str:
        return self._dump(task)

    def render_tasks(self, tasks: list[dict]) -> str:
        return self._dump({"tasks": tasks})

    def render_memory(self, memory: dict) -> str:
        return self._dump(memory)

    def render_memories(self, memories: list[dict]) -> str:
        return self._dump({"memories": memories})

    def render_feedback(self, event: dict) -> str:
        return self._dump(event)

    def render_feedback_list(self, events: list[dict]) -> str:
        return self._dump({"feedback": events})

    def render_privacy(self, payload: dict) -> str:
        return self._dump(payload)

    def render_doctor(self, payload: dict) -> str:
        return self._dump(payload)

    def render_kv(self, title: str, payload: dict) -> str:
        return self._dump({title: payload})


class TextFormatter:
    """Human-readable formatter with optional ANSI color."""

    def __init__(self, color: bool = True):
        self.color = color and console.supports_color()

    def _dim(self, text: str) -> str:
        return console.colorize(text, console.DIM, self.color)

    def _accent(self, text: str, color: str) -> str:
        return console.colorize(text, color, self.color)

    def render_decision(self, decision: dict) -> str:
        action = decision.get("action", "?")
        lines = [
            f"{self._accent('decision', console.BOLD)} {decision.get('decision_id', '')}",
            f"  action:      {self._accent(action, console.action_color(action))}",
            f"  reasons:     {', '.join(decision.get('reason_codes', [])) or '-'}",
            f"  confidence:  {decision.get('confidence', 0):.2f}",
            f"  scope:       {decision.get('scope', 'local')}",
            f"  policy:      {decision.get('policy_version', '?')}",
            f"  requires:    {'user' if decision.get('requires_user') else 'auto'}",
        ]
        return "\n".join(lines)

    def render_task(self, task: dict) -> str:
        status = task.get("status", "?")
        lines = [
            f"{self._accent('task', console.BOLD)} {task.get('task_id', '')}",
            f"  goal:        {task.get('goal', '')}",
            f"  status:      {self._accent(status, console.BLUE if status != 'failed' else console.RED)}",
            f"  phase:       {task.get('current_phase') or '-'}",
        ]
        if task.get("completed_steps"):
            lines.append(f"  completed:   {', '.join(task['completed_steps'])}")
        if task.get("pending_steps"):
            lines.append(f"  pending:     {', '.join(task['pending_steps'])}")
        if task.get("blocked_reason"):
            lines.append(f"  blocked:     {self._accent(task['blocked_reason'], console.RED)}")
        lines.append(f"  created:     {task.get('created_at', '')}")
        lines.append(f"  updated:     {task.get('updated_at', '')}")
        return "\n".join(lines)

    def render_tasks(self, tasks: list[dict]) -> str:
        if not tasks:
            return self._dim("no tasks.")
        header = f"{'task_id':<20} {'status':<22} {'phase':<14} goal"
        rows = [self._accent(header, console.BOLD)]
        for task in tasks:
            rows.append(
                f"{task.get('task_id', ''):<20} "
                f"{task.get('status', ''):<22} "
                f"{(task.get('current_phase') or '-'):<14} "
                f"{task.get('goal', '')}"
            )
        return "\n".join(rows)

    def render_memory(self, memory: dict) -> str:
        status = memory.get("status", "?")
        privacy = memory.get("privacy", "?")
        score = memory.get("score")
        lines = [
            f"{self._accent('memory', console.BOLD)} {memory.get('memory_id', '')}",
            f"  status:      {self._accent(status, console.action_color('consolidate_memory') if status == 'active' else console.GREY)}",
            f"  privacy:     {self._accent(privacy, console.level_color(privacy))}",
            f"  type:        {memory.get('memory_type', '')}",
        ]
        if score is not None:
            lines.append(f"  score:       {score:.3f}")
        lines.append(f"  content:     {memory.get('content', '')}")
        if memory.get("keywords"):
            lines.append(f"  keywords:    {', '.join(memory['keywords'])}")
        lines.append(f"  accesses:    {memory.get('access_count', 0)}")
        return "\n".join(lines)

    def render_memories(self, memories: list[dict]) -> str:
        if not memories:
            return self._dim("no memories.")
        header = f"{'memory_id':<20} {'status':<10} {'privacy':<12} {'score':>6}  content"
        rows = [self._accent(header, console.BOLD)]
        for memory in memories:
            score = memory.get("score")
            score_text = f"{score:>6.3f}" if isinstance(score, (int, float)) else f"{'-':>6}"
            content = (memory.get("content", "") or "").replace("\n", " ")
            if len(content) > 60:
                content = content[:57] + "..."
            rows.append(
                f"{memory.get('memory_id', ''):<20} "
                f"{memory.get('status', ''):<10} "
                f"{memory.get('privacy', ''):<12} "
                f"{score_text}  {content}"
            )
        return "\n".join(rows)

    def render_feedback(self, event: dict) -> str:
        return self.render_kv("feedback", event)

    def render_feedback_list(self, events: list[dict]) -> str:
        if not events:
            return self._dim("no feedback.")
        header = f"{'feedback_id':<22} {'label':<10} {'signal':<16} task"
        rows = [self._accent(header, console.BOLD)]
        for event in events:
            rows.append(
                f"{event.get('feedback_id', ''):<22} "
                f"{event.get('label', ''):<10} "
                f"{event.get('signal', ''):<16} "
                f"{event.get('task_id') or '-'}"
            )
        return "\n".join(rows)

    def render_privacy(self, payload: dict) -> str:
        level = payload.get("level", "?")
        matches = payload.get("matches", [])
        lines = [
            f"{self._accent('privacy scan', console.BOLD)}",
            f"  level:       {self._accent(level, console.level_color(level))}",
        ]
        if matches:
            lines.append(f"  matches:     {', '.join(matches)}")
        else:
            lines.append("  matches:     -")
        if payload.get("blocked"):
            lines.append(self._accent("  result:      blocked from remote transfer", console.RED))
        elif matches:
            lines.append(self._accent("  result:      redacted before remote transfer", console.YELLOW))
        else:
            lines.append(self._accent("  result:      safe", console.GREEN))
        redacted = payload.get("redacted")
        if redacted and matches:
            lines.append(f"  redacted:    {redacted}")
        return "\n".join(lines)

    def render_doctor(self, payload: dict) -> str:
        ok = payload.get("ok", False)
        banner = self._accent("OK", console.GREEN) if ok else self._accent("FAIL", console.RED)
        lines = [
            f"{self._accent('agent-compass', console.BOLD)} doctor",
            f"  status:      {banner}",
            f"  version:     {payload.get('version', '?')}",
            f"  policy:      {payload.get('policy_version', '?')}",
            f"  schema:      {payload.get('schema_version', '?')}",
            f"  data_dir:    {payload.get('data_dir', '?')}",
        ]
        return "\n".join(lines)

    def render_kv(self, title: str, payload: dict) -> str:
        lines = [self._accent(title, console.BOLD)]
        for key, value in payload.items():
            lines.append(f"  {key:<12} {value}")
        return "\n".join(lines)


def make_formatter(fmt: str, color: bool = True) -> Formatter:
    if fmt == "json":
        return JsonFormatter()
    if fmt == "text":
        return TextFormatter(color=color)
    raise ValueError(f"unknown format: {fmt!r}; expected 'json' or 'text'")
