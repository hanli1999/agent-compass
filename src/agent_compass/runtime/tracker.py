"""The four v3 fields, maintained automatically.

A host that uses this class does not have to count its own silent
answers or remember its own recent tool calls. The two methods
:meth:`record_action` and :meth:`record_answer` are the only thing
the host loop has to call.

The tracker is *deliberately* in-memory only. Persistence is the
host's job (e.g. a JSON sidecar the host writes between turns) —
the policy engine only consults the in-memory snapshot, and an
in-memory snapshot is the only thing that cannot lie about what
just happened.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Any


#: How many recent actions we keep around. The engine only reads the
#: last five, but a longer window lets the host render "what I have
#: been doing" without keeping a separate buffer.
DEFAULT_WINDOW = 20


@dataclass(frozen=True)
class TrackerSnapshot:
    """The four v3 fields, frozen so a host can pass them safely.

    The fields are exactly the four that ``DecisionContext`` reads
    when v3 is enabled. A host that wants to override one (e.g. a
    forced complexity score) can do so by passing the override to
    :meth:`AutoTracker.snapshot`; the override is applied to the
    returned snapshot, not to the underlying state.
    """

    consecutive_answer_directly: int = 0
    recent_actions: tuple[str, ...] = ()
    complexity_score: float = 0.0
    uncertainty_score: float = 0.0


class AutoTracker:
    """In-memory state for the four v3 fields.

    The tracker counts *consecutive* ``record_answer`` calls. The
    first ``record_action`` resets the counter. A host loop that
    alternates "think, think, act, think, think, think" gets
    ``consecutive_answer_directly=3`` after the third "think",
    which is what the engine needs to break the silent-thinking
    loop.

    ``recent_actions`` is a sliding window. Older entries are
    dropped; newer entries push older ones out. The engine only
    looks at the last 5, but we keep 20 by default so a human
    operator can ask "what have I been doing?" without losing
    history.

    ``complexity_score`` and ``uncertainty_score`` are *not* derived
    here. They are subjective — the host (or its model) is the
    only thing that knows how complex the current task is and how
    confident it is in its own answer. The tracker holds whatever
    the host passed to :meth:`set_complexity` /
    :meth:`set_uncertainty`. A host that wants heuristics can wrap
    those setters; the tracker does not invent scores.
    """

    def __init__(
        self,
        *,
        window: int = DEFAULT_WINDOW,
        initial_recent_actions: Iterable[str] | None = None,
    ) -> None:
        if window < 1:
            raise ValueError("window must be at least 1")
        self._window = int(window)
        self._consecutive_answer_directly = 0
        self._recent_actions: list[str] = list(initial_recent_actions or [])[-self._window :]
        self._complexity_score = 0.0
        self._uncertainty_score = 0.0

    # ---- introspection -------------------------------------------------

    @property
    def consecutive_answer_directly(self) -> int:
        return self._consecutive_answer_directly

    @property
    def recent_actions(self) -> tuple[str, ...]:
        return tuple(self._recent_actions)

    @property
    def complexity_score(self) -> float:
        return self._complexity_score

    @property
    def uncertainty_score(self) -> float:
        return self._uncertainty_score

    # ---- mutation ------------------------------------------------------

    def record_action(self, name: str) -> None:
        """Record that the host just called a tool named ``name``.

        Resets the silent-thinking counter because the host has
        *acted* — the engine's job is to spot the opposite, not
        to flag normal tool use.

        Empty / whitespace-only names are dropped. A name that is
        not a string is coerced via ``str()`` so a host that
        passes a Path or an enum does not have to flatten first.
        """
        clean = str(name or "").strip()
        if not clean:
            return
        self._recent_actions.append(clean)
        if len(self._recent_actions) > self._window:
            # Keep the most recent ``window`` entries.
            del self._recent_actions[: len(self._recent_actions) - self._window]
        self._consecutive_answer_directly = 0

    def record_answer(self) -> None:
        """Record that the host produced a plain answer (no tool call).

        Increments the silent-thinking counter. The engine reads
        this counter to decide when to nudge the host with
        ``action_pressure`` (the v3 "you have been silent for too
        long" branch).
        """
        self._consecutive_answer_directly += 1

    def set_complexity(self, score: float) -> None:
        """Override the complexity score the host will report to the engine.

        Values are clamped to ``[0.0, 1.0]``. A host that wants the
        tracker to invent a score from heuristics should not call
        this method; the tracker is a passive holder.
        """
        if score < 0.0:
            score = 0.0
        elif score > 1.0:
            score = 1.0
        self._complexity_score = float(score)

    def set_uncertainty(self, score: float) -> None:
        """Override the uncertainty score the host will report to the engine.

        Same clamping rule as :meth:`set_complexity`.
        """
        if score < 0.0:
            score = 0.0
        elif score > 1.0:
            score = 1.0
        self._uncertainty_score = float(score)

    def reset(self) -> None:
        """Forget all state. Useful between unrelated sessions."""
        self._consecutive_answer_directly = 0
        self._recent_actions.clear()
        self._complexity_score = 0.0
        self._uncertainty_score = 0.0

    # ---- persistence ---------------------------------------------------
    #
    # The tracker is in-memory by default. A host that wants to
    # remember the silent-thinking counter and the recent-actions
    # window across sessions calls ``flush_to(path)`` from a Stop
    # hook or a shutdown handler, and ``restore_from(path)`` from a
    # SessionStart hook. The format is a single-line JSON object;
    # restoring is a no-op if the file does not exist.

    def to_dict(self) -> dict[str, Any]:
        """Return the tracker state as a JSON-friendly dict.

        Keys mirror the four v3 fields plus a ``schema_version`` so a
        future format change can detect a mismatch instead of silently
        re-scoring.
        """
        return {
            "schema_version": 1,
            "consecutive_answer_directly": self._consecutive_answer_directly,
            "recent_actions": list(self._recent_actions),
            "complexity_score": self._complexity_score,
            "uncertainty_score": self._uncertainty_score,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """Replace the tracker state from a ``to_dict`` payload.

        Unknown keys are ignored; missing keys fall back to the neutral
        defaults. ``schema_version`` mismatches raise ``ValueError``
        rather than silently re-scoring.
        """
        version = data.get("schema_version", 1)
        if version != 1:
            raise ValueError(
                f"tracker snapshot schema_version={version} is not supported "
                "(this build understands schema_version=1)"
            )
        self._consecutive_answer_directly = int(data.get("consecutive_answer_directly", 0))
        recent = data.get("recent_actions", []) or []
        self._recent_actions = list(recent)[-self._window :]
        self._complexity_score = _clamp(float(data.get("complexity_score", 0.0)))
        self._uncertainty_score = _clamp(float(data.get("uncertainty_score", 0.0)))

    def flush_to(self, path: str | Path) -> None:
        """Write the current state to ``path`` as a single JSON object.

        Parent directories are created if they do not exist. The write
        is atomic: the file is written to ``path + ".tmp"`` first and
        then renamed, so a crash mid-flush cannot leave the host with
        a half-written state file.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    def restore_from(self, path: str | Path) -> bool:
        """Restore the tracker state from ``path`` if it exists.

        Returns ``True`` when state was loaded, ``False`` when the
        file did not exist (the tracker keeps its current state).
        Corrupt JSON raises ``ValueError`` so the host can decide
        whether to fall back to neutral or fail closed.
        """
        target = Path(path)
        if not target.exists():
            return False
        self.from_dict(json.loads(target.read_text(encoding="utf-8")))
        return True

    # ---- snapshot ------------------------------------------------------

    def snapshot(
        self,
        *,
        complexity: float | None = None,
        uncertainty: float | None = None,
    ) -> TrackerSnapshot:
        """Return a frozen view of the current state for ``DecisionContext``.

        ``complexity`` and ``uncertainty`` overrides are *per-call* —
        they are returned on the snapshot but do not mutate the
        tracker. A host that wants to set a baseline uses
        :meth:`set_complexity`; a host that wants a one-off override
        (e.g. "this turn is unusually complex") passes the score
        here.
        """
        return TrackerSnapshot(
            consecutive_answer_directly=self._consecutive_answer_directly,
            recent_actions=tuple(self._recent_actions),
            complexity_score=self._complexity_score if complexity is None else _clamp(complexity),
            uncertainty_score=self._uncertainty_score if uncertainty is None else _clamp(uncertainty),
        )


def _clamp(score: float) -> float:
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return float(score)


__all__ = ["AutoTracker", "DEFAULT_WINDOW", "TrackerSnapshot"]
