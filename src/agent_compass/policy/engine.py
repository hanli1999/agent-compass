"""A deterministic, explainable policy engine."""
from __future__ import annotations

from ..config import CompassConfig
from ..models import Decision, DecisionAction, DecisionContext


class PolicyEngine:
    """Decide whether an agent should retrieve, ask, continue, or pause.

    This layer never executes tools and never contacts a model. A caller may use
    an LLM for classification, but the final safety gates remain deterministic.
    """

    def __init__(self, config: CompassConfig | None = None):
        self.config = config or CompassConfig.from_env()

    def decide(self, context: DecisionContext) -> Decision:
        reasons: list[str] = []

        if context.waiting_for_approval:
            return Decision(DecisionAction.PAUSE_FOR_APPROVAL, ["approval_pending"], 1.0, True)

        if context.external_side_effect or context.destructive_action:
            reasons.append("external_side_effect" if context.external_side_effect else "destructive_action")
            return Decision(DecisionAction.PAUSE_FOR_APPROVAL, reasons, 0.99, True, "local")

        if context.task_in_progress:
            reasons.append("task_in_progress")
            return Decision(DecisionAction.RESUME, reasons, 0.96, False, "local")

        if context.ambiguity >= 0.7:
            return Decision(DecisionAction.ASK_USER, ["ambiguous_goal"], 0.9, True)

        needs_retrieval = (
            context.explicit_search_request
            or context.time_sensitive
            or not context.has_sufficient_context
        )
        if needs_retrieval:
            reasons.extend(self._retrieval_reasons(context))
            scope = "remote" if context.remote_allowed else "local"
            if scope == "local" and context.remote_allowed is False and context.explicit_search_request:
                reasons.append("remote_not_allowed")
            return Decision(DecisionAction.RETRIEVE, reasons, 0.9, False, scope)

        return Decision(DecisionAction.ANSWER_DIRECTLY, ["sufficient_context"], 0.92, False, "local")

    def _retrieval_reasons(self, context: DecisionContext) -> list[str]:
        reasons = []
        if context.explicit_search_request:
            reasons.append("explicit_search_request")
        if context.time_sensitive:
            reasons.append("time_sensitive")
        if not context.has_sufficient_context:
            reasons.append("context_insufficient")
        return reasons or ["retrieval_requested"]
