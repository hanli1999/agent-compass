"""The thin glue that turns a Compass into a self-driving loop.

A host that uses :class:`HostLoop` does not have to know about
``DecisionContext`` or the four v3 fields. The two methods
:meth:`decide` and :meth:`record` are the only thing the host loop
calls. The wrapper

* feeds the tracker's snapshot into the ``DecisionContext`` on
  every call;
* after a ``decide``, mirrors the action back into the tracker so
  the host does not have to remember to call :meth:`record_action`
  on the engine's behalf;
* holds onto the last ``Decision`` so the host can render it or
  log it without re-running the policy engine.

The class is intentionally tiny. Anything more clever — running
the action, mapping ``EXPLORE`` to a ReAct loop, etc. — belongs in
the host, not in the policy layer.
"""
from __future__ import annotations

from typing import Any, Iterable

from .. import Compass
from ..models import Decision, DecisionContext, DecisionAction
from .tracker import AutoTracker, TrackerSnapshot


class HostLoop:
    """Wrap a :class:`Compass` with an :class:`AutoTracker`.

    Parameters
    ----------
    compass:
        The Compass instance to drive. Its ``CompassConfig`` should
        have v3 enabled (call :func:`apply_smart_defaults` first if
        in doubt) so the engine actually consults the tracker.
    tracker:
        An existing tracker, or ``None`` to create a fresh one. A
        host that wants to share state across two ``Compass``
        instances (e.g. a foreground and a background summariser)
        can pass the same tracker to both loops.
    """

    def __init__(self, compass: Compass, *, tracker: AutoTracker | None = None):
        self.compass = compass
        self.tracker = tracker or AutoTracker()
        self._last_decision: Decision | None = None

    # ---- introspection -------------------------------------------------

    @property
    def last_decision(self) -> Decision | None:
        return self._last_decision

    def explain(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the loop's current state.

        Useful for ``/status``-style commands and for the
        ``UserPromptSubmit`` hook's diagnostic output.
        """
        snapshot = self.tracker.snapshot()
        return {
            "policy_version": self.compass.config.policy_v3_enabled and "policy-v3" or "policy-v2",
            "remote_allowed": bool(self.compass.config.remote_allowed),
            "tracker": {
                "consecutive_answer_directly": snapshot.consecutive_answer_directly,
                "recent_actions": list(snapshot.recent_actions),
                "complexity_score": snapshot.complexity_score,
                "uncertainty_score": snapshot.uncertainty_score,
            },
            "last_decision": self._last_decision.to_dict() if self._last_decision else None,
        }

    # ---- the loop ------------------------------------------------------

    def decide(
        self,
        user_input: str,
        *,
        task_id: str | None = None,
        complexity: float | None = None,
        uncertainty: float | None = None,
        recent_actions: Iterable[str] | None = None,
        **overrides: Any,
    ) -> Decision:
        """Run the policy engine with the tracker's snapshot folded in.

        ``complexity`` and ``uncertainty`` are per-call overrides —
        they replace the tracker's stored value for *this* call but
        do not mutate the tracker. ``recent_actions`` (when given)
        extends the tracker's window; the union is passed to the
        engine. Anything in ``overrides`` is forwarded to
        ``DecisionContext`` unchanged (so a host can pass
        ``remote_allowed=True`` etc. without re-typing the plumbing).
        """
        snapshot = self.tracker.snapshot(
            complexity=complexity,
            uncertainty=uncertainty,
        )
        merged_recent = list(snapshot.recent_actions)
        if recent_actions:
            merged_recent.extend(str(a) for a in recent_actions if a)
        ctx = DecisionContext(
            user_input=user_input,
            task_id=task_id,
            complexity_score=snapshot.complexity_score,
            uncertainty_score=snapshot.uncertainty_score,
            consecutive_answer_directly=snapshot.consecutive_answer_directly,
            recent_actions=merged_recent[-5:],
            **overrides,
        )
        decision = self.compass.decide(ctx)
        self._last_decision = decision
        # Mirror the decision back into the tracker so the host loop
        # does not have to remember to call ``record_action`` for
        # the actions the engine actually emitted.
        self._mirror(decision)
        return decision

    def record(self, kind: str) -> None:
        """Tell the tracker that a tool call (or answer) just happened.

        ``kind`` is one of:

        * the name of a tool the host actually called (e.g.
          ``"retrieve"``, ``"web_search"``) — wraps
          :meth:`AutoTracker.record_action`;
        * the literal string ``"answer"`` — wraps
          :meth:`AutoTracker.record_answer`.

        Any other string is treated as a tool action. An empty
        string is a no-op.
        """
        if not kind:
            return
        if kind == "answer":
            self.tracker.record_answer()
            return
        self.tracker.record_action(kind)

    # ---- private -------------------------------------------------------

    def _mirror(self, decision: Decision) -> None:
        """Update the tracker from a Decision the engine just emitted.

        The engine's action is *about* what the host should do next,
        not what it just did. We mirror it so the *next* call to
        :meth:`decide` has an accurate ``recent_actions`` view, but
        we do not pretend the host has already executed the
        action — that is the host's job, and it will tell us via
        :meth:`record` when it does.
        """
        action = decision.action
        if action in {
            DecisionAction.RETRIEVE,
            DecisionAction.EXPLORE,
            DecisionAction.RETRIEVE_THEN_ACT,
        }:
            # Record the action we *suggested*, not the action we
            # confirmed. The host's eventual ``record`` call is the
            # confirmation; this mirror is just so subsequent
            # decisions see the suggestion in their recent window.
            self.tracker.record_action(action.value)
        elif action is DecisionAction.ANSWER_DIRECTLY:
            self.tracker.record_answer()


__all__ = ["HostLoop"]
