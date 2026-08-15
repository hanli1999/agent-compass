"""Direct unit tests for the v0.7.0 EXPLORE branch.

The golden tests in ``tests/golden/`` pin the engine output end-to-end.
These tests focus on the EXPLORE branch in particular — its gating
conditions and the way it interacts with the other v3 branches.
"""
from __future__ import annotations

from agent_compass import Compass, CompassConfig
from agent_compass.models import DecisionAction, DecisionContext


def _compass(remote: bool = True) -> Compass:
    return Compass(CompassConfig(data_dir=__import__("pathlib").Path("/tmp"), policy_v3_enabled=True, remote_allowed=remote))


def test_explore_fires_on_high_complexity_with_remote():
    d = _compass().decide(DecisionContext(
        user_input="what changed in fastapi 0.118",
        complexity_score=0.8,
        remote_allowed=True,
        has_sufficient_context=True,
    ))
    assert d.action is DecisionAction.EXPLORE
    assert d.scope == "remote"
    assert d.policy_version == "policy-v3"
    assert any("explore" in code for code in d.reason_codes)


def test_explore_fires_on_high_uncertainty_with_remote():
    d = _compass().decide(DecisionContext(
        user_input="what changed in fastapi 0.118",
        uncertainty_score=0.7,
        remote_allowed=True,
        has_sufficient_context=True,
    ))
    assert d.action is DecisionAction.EXPLORE


def test_explore_does_not_fire_without_remote_flag():
    d = _compass(remote=False).decide(DecisionContext(
        user_input="what changed in fastapi 0.118",
        complexity_score=0.8,
        remote_allowed=True,
        has_sufficient_context=True,
    ))
    # EXPLORE is gated by config.remote_allowed too — without it the
    # engine falls back to the local RETRIEVE_THEN_ACT.
    assert d.action is DecisionAction.RETRIEVE_THEN_ACT
    assert d.scope == "local"


def test_explore_does_not_fire_if_remote_allowed_missing_on_context():
    d = _compass().decide(DecisionContext(
        user_input="what changed in fastapi 0.118",
        complexity_score=0.8,
        remote_allowed=False,
        has_sufficient_context=True,
    ))
    assert d.action is not DecisionAction.EXPLORE


def test_explore_does_not_fire_after_recent_web_search():
    d = _compass().decide(DecisionContext(
        user_input="follow up",
        complexity_score=0.8,
        remote_allowed=True,
        has_sufficient_context=True,
        recent_actions=["web_search", "answer_directly"],
    ))
    # The host has already searched this turn; the engine should not
    # re-suggest an outer action.
    assert d.action is not DecisionAction.EXPLORE


def test_explore_below_thresholds_does_not_fire():
    d = _compass().decide(DecisionContext(
        user_input="small change",
        complexity_score=0.4,
        uncertainty_score=0.2,
        remote_allowed=True,
        has_sufficient_context=True,
    ))
    assert d.action is not DecisionAction.EXPLORE
    assert d.action is DecisionAction.ANSWER_DIRECTLY


def test_explore_does_not_run_when_v3_disabled():
    compass = Compass(CompassConfig(data_dir=__import__("pathlib").Path("/tmp"), policy_v3_enabled=False, remote_allowed=True))
    d = compass.decide(DecisionContext(
        user_input="what changed in fastapi 0.118",
        complexity_score=0.9,
        uncertainty_score=0.9,
        remote_allowed=True,
        has_sufficient_context=True,
    ))
    assert d.action is not DecisionAction.EXPLORE
    # Falls through to v2 with sufficient context: ANSWER_DIRECTLY.
    assert d.action is DecisionAction.ANSWER_DIRECTLY
