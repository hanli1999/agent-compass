"""Host-side helpers that turn Agent Compass into a self-driving loop.

Without this package a host has to do three things by hand:

1. Opt in to ``policy_v3_enabled``.
2. Track ``consecutive_answer_directly`` and ``recent_actions`` so
   the engine can spot a silent-thinking loop.
3. Wire a web adapter into ``RetrievalOrchestrator`` so an
   ``EXPLORE`` decision has somewhere to land.

The package ships the boring half of each so a fresh host can
``compass = Compass(...)`` and the rest is already on.

Public surface
--------------

* :class:`AutoTracker` — small in-memory state for the four
  v3-aware fields. The host only has to call
  :meth:`record_action` when it calls a tool, and
  :meth:`record_answer` when it speaks. Everything else is derived.
* :class:`HostLoop` — wraps a :class:`Compass` with an
  :class:`AutoTracker` and exposes :meth:`decide` and
  :meth:`record`. The two methods are the only thing a host loop
  has to call.
* :func:`apply_smart_defaults` — flips ``policy_v3_enabled`` on,
  wires the :class:`DuckDuckGoAdapter` (if ``remote_allowed``),
  and sets sensible thresholds. Idempotent: calling it twice
  does the same thing as calling it once.
* :func:`install_claude_code_hooks` — writes the five Claude Code
  hook events to ``~/.claude/settings.json`` (or a path the caller
  picks) so a fresh install gets a working hook set without the
  operator having to merge anything.
"""
from __future__ import annotations

from .tracker import AutoTracker, TrackerSnapshot
from .loop import HostLoop
from .defaults import apply_smart_defaults, build_smart_default_config
from .hooks_install import install_claude_code_hooks

__all__ = [
    "AutoTracker",
    "HostLoop",
    "TrackerSnapshot",
    "apply_smart_defaults",
    "build_smart_default_config",
    "install_claude_code_hooks",
]
