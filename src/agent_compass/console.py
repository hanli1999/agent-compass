"""Minimal ANSI color helpers with no external dependencies.

The Agent Compass CLI is meant to stay portable across local shells, CI logs,
and Windows terminals. This module deliberately uses raw ANSI escape codes
so we can keep ``pyproject.toml`` free of optional runtime dependencies.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable

# Basic 8-color palette. We avoid 256-color or truecolor because not every
# terminal honors them; the SGR codes below are the universal subset.
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
GREY = "\x1b[90m"

_LEVEL_COLOR: dict[str, str] = {
    "public": GREEN,
    "local_only": BLUE,
    "sensitive": YELLOW,
    "secret": RED,
}

_ACTION_COLOR: dict[str, str] = {
    "answer_directly": GREEN,
    "retrieve": CYAN,
    "ask_user": YELLOW,
    "continue": BLUE,
    "pause_for_approval": RED,
    "resume": MAGENTA,
    "consolidate_memory": CYAN,
    "stop": RED,
}


def supports_color(stream=None) -> bool:
    """Best-effort detection of whether ANSI escapes are safe to emit."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("AGENT_COMPASS_NO_COLOR"):
        return False
    if os.environ.get("AGENT_COMPASS_FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    if not hasattr(stream, "isatty"):
        return False
    if not stream.isatty():
        return False
    # Windows: enable VT processing when we can.
    if sys.platform == "win32":
        try:
            import ctypes

            kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | 0x0004)
                return True
        except Exception:
            return False
    return True


def colorize(text: str, color: str, enabled: bool = True) -> str:
    if not enabled or not color:
        return text
    return f"{color}{text}{RESET}"


def level_color(level: str) -> str:
    return _LEVEL_COLOR.get(level.lower(), "")


def action_color(action: str) -> str:
    return _ACTION_COLOR.get(action.lower(), "")


def horizontal_rule(width: int = 60, char: str = "─", enabled: bool = True) -> str:
    rule = char * max(1, width)
    return colorize(rule, DIM, enabled)


def join_lines(lines: Iterable[str]) -> str:
    return "\n".join(lines)
