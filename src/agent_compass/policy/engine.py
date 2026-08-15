"""A deterministic, explainable policy engine."""
from __future__ import annotations

from ..config import CompassConfig
from ..models import (
    Decision,
    DecisionAction,
    DecisionContext,
    SessionState,
)


class PolicyEngine:
    """Decide whether an agent should retrieve, ask, continue, or pause.

    This layer never executes tools and never contacts a model. A caller may use
    an LLM for classification, but the final safety gates remain deterministic.

    Decision order under ``policy-v2`` (default, unchanged since 0.2.0):

    1. ``approval_pending`` -> ``PAUSE_FOR_APPROVAL`` (state held in task store)
    2. ``external_side_effect`` / ``destructive_action`` -> ``PAUSE_FOR_APPROVAL``
    3. retry budget exhausted -> ``STOP`` (no silent infinite retry)
    4. session ending -> ``CONSOLIDATE_MEMORY`` (capture learnings before exit)
    5. ``interrupted`` task in progress -> ``RESUME`` (continue from checkpoint)
    6. ``ambiguity`` over threshold -> ``ASK_USER``
    7. retrieval needed -> ``RETRIEVE`` (remote gated by config + flags)
    8. otherwise -> ``ANSWER_DIRECTLY``

    Decision order under ``policy-v3`` (opt-in, since 0.6.0). Two new action-bias
    branches sit *before* the legacy ASK_USER / RETRIEVE / ANSWER_DIRECTLY
    order. They only fire when ``config.policy_v3_enabled`` is True AND the host
    has populated the new ``complexity_score`` / ``uncertainty_score`` /
    ``consecutive_answer_directly`` fields. A v3-enabled engine that sees only
    legacy fields degrades to v2 behaviour transparently — the only externally
    visible difference is the ``policy_version`` string on the returned
    Decision.

    v3 additional branches (inserted between step 5 and step 6 of v2, in
    this order):

    A. ``consecutive_answer_directly >= action_pressure_threshold`` ->
       ``RETRIEVE_THEN_ACT`` ("you have been silent for too long, take an action")
    D. ``(complexity >= threshold OR uncertainty >= threshold)`` AND no
       ``web_search`` in ``recent_actions[-5:]`` AND ``remote_allowed`` ->
       ``EXPLORE`` (this needs outside information; do a ReAct-style
       web_search -> inspect -> maybe web_fetch -> answer loop). v0.7.0+.
    B. ``uncertainty_score >= uncertainty_threshold`` ->
       ``RETRIEVE`` (your self-report says you don't actually know).
       Fires only when EXPLORE did not (offline, or recent web_search).
    C. ``complexity_score >= complexity_threshold`` AND no retrieval in
       ``recent_actions`` -> ``RETRIEVE_THEN_ACT`` (this is multi-step, gather
       first, then act). Fires only when EXPLORE did not (offline).

    The order is A → D → B → C: *pressure beats the web beats self-doubt
    beats planning*. Pressure means "do anything" — do not even pause to
    ask whether a search is needed. The web supersedes local retrieve
    when remote is actually allowed and the host has not yet searched.
    Self-doubt and planning only fire when the web path is unavailable.

    If multiple branches fire, the one listed first wins. ``EXPLORE`` is
    the only v3 branch that *requires* ``remote_allowed``; the other
    three keep working offline.
    """

    def __init__(self, config: CompassConfig | None = None):
        self.config = config or CompassConfig.from_env()

    def decide(self, context: DecisionContext) -> Decision:
        reasons: list[str] = []
        threshold = self.config.ambiguity_threshold

        if context.waiting_for_approval:
            return Decision(DecisionAction.PAUSE_FOR_APPROVAL, ["approval_pending"], 1.0, True)

        destructive, destructive_match = self._is_destructive(context)
        external = context.external_side_effect
        if external or destructive:
            code = "external_side_effect" if external else f"destructive_action:{destructive_match}"
            return Decision(DecisionAction.PAUSE_FOR_APPROVAL, [code], 0.99, True, "local")

        budget = context.retry_budget if context.retry_budget is not None else self.config.max_retries
        if context.retry_count >= budget:
            return Decision(
                DecisionAction.STOP,
                ["retry_budget_exhausted", f"streak={context.failure_streak}"],
                0.95,
                False,
                "local",
            )

        if context.session_state in {SessionState.ENDING, SessionState.ENDED}:
            reasons.append(f"session_{context.session_state.value}")
            if context.last_error:
                reasons.append("last_error_present")
            return Decision(DecisionAction.CONSOLIDATE_MEMORY, reasons, 0.9, False, "local")

        if context.task_in_progress and context.interrupted:
            reasons.append("task_interrupted")
            return Decision(DecisionAction.RESUME, reasons, 0.96, False, "local")

        if context.task_in_progress:
            reasons.append("task_in_progress")
            return Decision(DecisionAction.CONTINUE, reasons, 0.96, False, "local")

        # ---- policy-v3 action-bias branches (opt-in) ----
        v3 = self.config.policy_v3_enabled
        if v3:
            # A. action pressure — three silent answers in a row is a loop.
            pressure = self.config.action_pressure_threshold
            if context.consecutive_answer_directly >= pressure:
                return Decision(
                    DecisionAction.RETRIEVE_THEN_ACT,
                    [
                        "action_pressure",
                        f"consecutive_answer_directly={context.consecutive_answer_directly}>={pressure}",
                    ],
                    0.88,
                    False,
                    "local",
                    policy_version="policy-v3",
                )

            # D. EXPLORE — v0.7.0+. Fires *before* B and C because it is
            #    the superset: when remote is allowed and the host has
            #    not yet asked the open web, "go to the web" subsumes
            #    "retrieve" and "retrieve_then_act". Two gates keep it
            #    tight:
            #      1. remote is actually allowed (config + caller flag);
            #      2. the recent-action window shows no web_search. A host
            #         that already searched this turn is left alone.
            threshold_uncertainty = self.config.uncertainty_threshold
            threshold_complexity = self.config.complexity_threshold
            wants_explore = (
                context.complexity_score >= threshold_complexity
                or context.uncertainty_score >= threshold_uncertainty
            )
            if (
                wants_explore
                and not self._searched_recently(context.recent_actions)
                and context.remote_allowed
                and self.config.remote_allowed
            ):
                code = (
                    "complexity_explore"
                    if context.complexity_score >= threshold_complexity
                    else "uncertainty_explore"
                )
                return Decision(
                    DecisionAction.EXPLORE,
                    [
                        code,
                        f"complexity={context.complexity_score:.2f}",
                        f"uncertainty={context.uncertainty_score:.2f}",
                    ],
                    0.9,
                    False,
                    "remote",
                    policy_version="policy-v3",
                )

            # B. self-reported uncertainty — bypass the legacy "if sufficient
            #    context" branch. The host already told us it does not have
            #    what it needs. Fires only when EXPLORE did not (i.e. remote
            #    not allowed, or recent web_search in the window).
            if context.uncertainty_score >= threshold_uncertainty:
                scope = "remote" if (context.remote_allowed and self.config.remote_allowed) else "local"
                return Decision(
                    DecisionAction.RETRIEVE,
                    [
                        "uncertainty_threshold",
                        f"uncertainty_score={context.uncertainty_score:.2f}>={threshold_uncertainty}",
                    ],
                    0.88,
                    False,
                    scope,
                    policy_version="policy-v3",
                )

            # C. complexity without recent retrieval — multi-step work should
            #    not be answered on the first internal scratchpad. Fires
            #    only when EXPLORE did not (offline mode) and the host has
            #    not already gathered (any flavor of retrieve/search/fetch
            #    in the last 5 actions).
            if (
                context.complexity_score >= threshold_complexity
                and not self._retrieved_recently(context.recent_actions)
            ):
                scope = "remote" if (context.remote_allowed and self.config.remote_allowed) else "local"
                return Decision(
                    DecisionAction.RETRIEVE_THEN_ACT,
                    [
                        "complexity_without_recent_retrieval",
                        f"complexity_score={context.complexity_score:.2f}>={threshold_complexity}",
                    ],
                    0.86,
                    False,
                    scope,
                    policy_version="policy-v3",
                )
        # ---- end v3 branches ----

        if context.ambiguity >= threshold:
            return Decision(DecisionAction.ASK_USER, [f"ambiguous_goal>={threshold}"], 0.9, True)

        time_sensitive, ts_match = self._is_time_sensitive(context)
        needs_retrieval = (
            context.explicit_search_request
            or time_sensitive
            or not context.has_sufficient_context
        )
        if needs_retrieval:
            reasons.extend(self._retrieval_reasons(context, time_sensitive, ts_match))
            scope = "remote" if (context.remote_allowed and self.config.remote_allowed) else "local"
            if scope == "local" and context.explicit_search_request and not self.config.remote_allowed:
                reasons.append("remote_not_allowed")
            return Decision(DecisionAction.RETRIEVE, reasons, 0.9, False, scope)

        return Decision(DecisionAction.ANSWER_DIRECTLY, ["sufficient_context"], 0.92, False, "local")

    def _is_time_sensitive(self, context: DecisionContext) -> tuple[bool, str | None]:
        if not self.config.auto_detect or context.time_sensitive:
            return bool(context.time_sensitive), None
        lowered = context.user_input.lower()
        for kw in self.config.time_sensitive_keywords:
            if kw.lower() in lowered:
                return True, kw
        return False, None

    def _is_destructive(self, context: DecisionContext) -> tuple[bool, str | None]:
        if context.destructive_action or context.external_side_effect:
            return True, None
        if not self.config.auto_detect:
            return False, None
        haystack = " ".join([context.user_input, *context.proposed_actions]).lower()
        for action in self.config.destructive_actions:
            if action.lower() in haystack:
                return True, action
        return False, None

    def _retrieval_reasons(
        self,
        context: DecisionContext,
        time_sensitive: bool,
        ts_match: str | None,
    ) -> list[str]:
        reasons: list[str] = []
        if context.explicit_search_request:
            reasons.append("explicit_search_request")
        if context.time_sensitive or time_sensitive:
            reasons.append("time_sensitive")
            if context.time_sensitive is False and ts_match:
                reasons.append(f"time_sensitive_keyword:{ts_match}")
        if not context.has_sufficient_context:
            reasons.append("context_insufficient")
        return reasons or ["retrieval_requested"]

    @staticmethod
    def _retrieved_recently(recent_actions: list[str]) -> bool:
        """Whether the host has done a memory / web retrieve in its last few steps.

        A ``recent_actions`` entry of ``"retrieve"`` or ``"web_search"`` (or any
        action whose name contains those substrings) counts. This is a thin
        heuristic by design — the host knows exactly what its actions are, but
        keeping the matcher in the engine lets us evolve it without changing
        every caller.
        """
        for name in recent_actions[-5:]:
            lowered = name.lower()
            if "retrieve" in lowered or "search" in lowered or "fetch" in lowered:
                return True
        return False

    @staticmethod
    def _searched_recently(recent_actions: list[str]) -> bool:
        """Whether the host has called a *web* search in its last few steps.

        Stricter than :meth:`_retrieved_recently` — it ignores plain
        memory ``retrieve`` and only counts entries whose name contains
        ``"web_search"``. ``EXPLORE`` should not fire if the host just
        ran a web search on its own; that would be a redundant
        suggestion. The default window is 5 actions.
        """
        for name in recent_actions[-5:]:
            if "web_search" in name.lower():
                return True
        return False
