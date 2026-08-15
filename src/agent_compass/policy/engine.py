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

    v3 additional branches (inserted between step 5 and step 6 of v2):

    A. ``consecutive_answer_directly >= action_pressure_threshold`` ->
       ``RETRIEVE_THEN_ACT`` ("you have been silent for too long, take an action")
    B. ``uncertainty_score >= uncertainty_threshold`` ->
       ``RETRIEVE`` (your self-report says you don't actually know)
    C. ``complexity_score >= complexity_threshold`` AND no retrieval in
       ``recent_actions`` -> ``RETRIEVE_THEN_ACT`` (this is multi-step, gather
       first, then act)

    If multiple branches fire, the one listed first wins. This mirrors the
    "action pressure beats self-doubt beats planning" ordering, which is what
    the dead-loop symptom actually calls for.
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

            # B. self-reported uncertainty — bypass the legacy "if sufficient
            #    context" branch. The host already told us it does not have
            #    what it needs.
            if context.uncertainty_score >= self.config.uncertainty_threshold:
                scope = "remote" if (context.remote_allowed and self.config.remote_allowed) else "local"
                return Decision(
                    DecisionAction.RETRIEVE,
                    [
                        "uncertainty_threshold",
                        f"uncertainty_score={context.uncertainty_score:.2f}>={self.config.uncertainty_threshold}",
                    ],
                    0.88,
                    False,
                    scope,
                    policy_version="policy-v3",
                )

            # C. complexity without recent retrieval — multi-step work should
            #    not be answered on the first internal scratchpad.
            threshold_complexity = self.config.complexity_threshold
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
