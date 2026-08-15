"""Install the five Claude Code hook events on a fresh host.

The companion to :mod:`agent_compass.runtime`. A host that calls
:func:`install_claude_code_hooks` on a clean machine gets a
working hook set without having to merge ``hooks/settings.example.json``
into ``~/.claude/settings.json`` by hand. The install is *additive*
— existing hooks under any of the five events are preserved, the
new ones are appended. A subsequent call is a no-op for the events
we already wired up.

The five events match the names Claude Code emits:

* ``SessionStart`` — runs ``agent-compass doctor`` so the host can
  surface a stale-data-dir warning before any policy decision is
  made, then ``agent-compass tracker restore`` so the v3 AutoTracker
  picks up the silent-thinking counter and recent-actions window
  from the last session.
* ``UserPromptSubmit`` — runs ``agent-compass decide --input "$PROMPT"``
  and writes the new ``last_task_id`` pointer so the eventual
  ``Stop`` hook knows which task to checkpoint.
* ``PreToolUse`` — runs ``agent-compass privacy scan`` over
  ``$TOOL_INPUT_PATH`` so a write/edit/bash with a secret in its
  payload is caught before the tool runs.
* ``PostToolUse`` — runs ``agent-compass feedback add`` in async
  mode. The call is non-blocking; the queue is flushed by the
  ``Stop`` hook.
* ``Stop`` — runs ``agent-compass task checkpoint --unspecified``,
  then ``agent-compass feedback flush``, then
  ``agent-compass tracker flush`` so the v3 state survives the
  restart.

The function returns a small report so an operator (or the
``doctor`` subcommand) can confirm what was installed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

#: The five events we wire up, in the order Claude Code documents them.
EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
)

#: The commands we install per event. ``"$var"`` placeholders are
#: filled in at runtime by Claude Code — we pass them through
#: verbatim, not interpolating them ourselves.
_EVENT_COMMANDS: dict[str, list[dict[str, Any]]] = {
    "SessionStart": [
        {
            "matcher": "startup|resume",
            "hooks": [
                {"type": "command", "command": "agent-compass doctor"},
                # v0.9.4+: pick up v3 state from the previous session.
                {"type": "command", "command": "agent-compass tracker restore"},
            ],
        },
    ],
    "UserPromptSubmit": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": 'agent-compass decide --input "$PROMPT" --time-sensitive',
                },
                {
                    "type": "command",
                    "command": 'agent-compass context set --task-id "$TASK_ID"',
                },
            ],
        },
    ],
    "PreToolUse": [
        {
            "matcher": "Write|Edit|Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": 'agent-compass privacy scan --input "$TOOL_INPUT_PATH"',
                },
            ],
        },
    ],
    "PostToolUse": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        'agent-compass feedback add --signal $TOOL_RESULT '
                        '--label neutral --notes "auto from hook"'
                    ),
                },
            ],
        },
    ],
    "Stop": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        'agent-compass task checkpoint --unspecified final '
                        '--note "session ended"'
                    ),
                },
                {
                    "type": "command",
                    "command": "agent-compass feedback flush",
                },
                # v0.9.4+: persist v3 state for the next session.
                {"type": "command", "command": "agent-compass tracker flush"},
            ],
        },
    ],
}


@dataclass
class HookInstallReport:
    """Summary of what :func:`install_claude_code_hooks` did."""

    settings_path: Path
    events_installed: list[str] = field(default_factory=list)
    events_skipped: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings_path": str(self.settings_path),
            "events_installed": list(self.events_installed),
            "events_skipped": list(self.events_skipped),
            "already_present": list(self.already_present),
        }


def install_claude_code_hooks(
    settings_path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> HookInstallReport:
    """Wire the five hook events into ``settings.json``.

    Parameters
    ----------
    settings_path:
        Path to write. Defaults to ``~/.claude/settings.json``. The
        parent directory is created on demand.
    overwrite:
        When True, replace any pre-existing hook entries on the
        five events. Default False: existing entries are preserved
        and our entries are appended.

    Returns
    -------
    HookInstallReport
        A small report describing what was installed. A host that
        wants to surface this in its own setup output can render
        ``report.to_dict()`` directly.
    """
    path = Path(settings_path) if settings_path else DEFAULT_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {}
    if path.exists() and not overwrite:
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            # Treat a corrupt file as empty; we do not want a
            # one-byte syntax error to brick a fresh install.
            settings = {}
    hooks_root = settings.setdefault("hooks", {})

    report = HookInstallReport(settings_path=path)

    for event in EVENTS:
        existing = hooks_root.get(event, []) or []
        if existing and not overwrite:
            # Add only the entries that are not already there.
            our_entries = _EVENT_COMMANDS[event]
            already = _already_present(existing, our_entries)
            if already:
                report.already_present.append(event)
                continue
            merged = list(existing) + list(our_entries)
            hooks_root[event] = merged
            report.events_installed.append(event)
        else:
            hooks_root[event] = list(_EVENT_COMMANDS[event])
            if existing:
                report.events_skipped.append(event)
            else:
                report.events_installed.append(event)

    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def _already_present(existing: list[dict[str, Any]], ours: list[dict[str, Any]]) -> bool:
    """Whether every one of ``ours`` is already represented in ``existing``.

    A "match" is *command-string identical* — the host loop changes
    when the commands change, so a different command string means
    a different intent.
    """
    existing_commands = {
        hook.get("command")
        for entry in existing
        for hook in entry.get("hooks", [])
        if hook.get("type") == "command"
    }
    ours_commands = [
        hook.get("command")
        for entry in ours
        for hook in entry.get("hooks", [])
        if hook.get("type") == "command"
    ]
    return all(cmd in existing_commands for cmd in ours_commands)


__all__ = ["EVENTS", "HookInstallReport", "install_claude_code_hooks"]
