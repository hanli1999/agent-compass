"""Bare hooks-install call.

Use this when the host only needs the Claude Code hooks wired and
nothing else from the SDK.

    pip install agent-compass
    python recipes/hooks_install.py            # writes ~/.claude/settings.json
    python recipes/hooks_install.py --print    # print what would happen, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from agent_compass.runtime import install_claude_code_hooks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="install agent-compass hooks")
    parser.add_argument("--settings", type=Path, default=None,
                        help="path to settings.json (default: ~/.claude/settings.json)")
    parser.add_argument("--print", action="store_true",
                        help="print what would happen, do not write")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace our entries instead of appending")
    args = parser.parse_args()

    if args.print:
        from agent_compass.runtime.hooks_install import EVENTS
        print(json.dumps({"would_install": list(EVENTS),
                          "settings_path": str(args.settings or "~/.claude/settings.json")},
                         indent=2))
        return 0

    report = install_claude_code_hooks(
        settings_path=args.settings,
        overwrite=args.overwrite,
    )
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())