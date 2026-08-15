"""Tests for the v0.8.0 host-runtime helpers.

Three pieces ship together: :class:`AutoTracker`,
:class:`HostLoop`, and :func:`apply_smart_defaults`. Each test
verifies one behaviour the rest of the runtime depends on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_compass import Compass, CompassConfig
from agent_compass.models import DecisionAction, DecisionContext
from agent_compass.runtime import (
    AutoTracker,
    HostLoop,
    apply_smart_defaults,
    build_smart_default_config,
)
from agent_compass.runtime.tracker import TrackerSnapshot


# ---- AutoTracker --------------------------------------------------------


def test_tracker_starts_neutral():
    t = AutoTracker()
    assert t.consecutive_answer_directly == 0
    assert t.recent_actions == ()
    assert t.complexity_score == 0.0
    assert t.uncertainty_score == 0.0


def test_tracker_record_action_increments_history():
    t = AutoTracker()
    t.record_action("retrieve")
    t.record_action("web_search")
    assert t.recent_actions == ("retrieve", "web_search")


def test_tracker_record_action_resets_silence_counter():
    t = AutoTracker()
    t.record_answer()
    t.record_answer()
    assert t.consecutive_answer_directly == 2
    t.record_action("retrieve")
    assert t.consecutive_answer_directly == 0


def test_tracker_record_answer_increments():
    t = AutoTracker()
    t.record_answer()
    t.record_answer()
    t.record_answer()
    assert t.consecutive_answer_directly == 3


def test_tracker_window_trims_old_entries():
    t = AutoTracker(window=3)
    for i in range(10):
        t.record_action(f"action_{i}")
    assert t.recent_actions == ("action_7", "action_8", "action_9")


def test_tracker_set_complexity_clamps():
    t = AutoTracker()
    t.set_complexity(2.0)
    assert t.complexity_score == 1.0
    t.set_complexity(-0.5)
    assert t.complexity_score == 0.0
    t.set_complexity(0.7)
    assert t.complexity_score == 0.7


def test_tracker_set_uncertainty_clamps():
    t = AutoTracker()
    t.set_uncertainty(2.0)
    assert t.uncertainty_score == 1.0
    t.set_uncertainty(-0.5)
    assert t.uncertainty_score == 0.0


def test_tracker_snapshot_is_frozen():
    t = AutoTracker()
    t.record_action("retrieve")
    snap = t.snapshot()
    assert isinstance(snap, TrackerSnapshot)
    assert snap.recent_actions == ("retrieve",)
    # The snapshot's tuple cannot be mutated.
    with pytest.raises((AttributeError, TypeError)):
        snap.recent_actions += ("oops",)  # type: ignore[arg-type]


def test_tracker_snapshot_overrides_do_not_mutate_state():
    t = AutoTracker()
    t.set_complexity(0.3)
    snap = t.snapshot(complexity=0.9)
    assert snap.complexity_score == 0.9
    assert t.complexity_score == 0.3  # state preserved


def test_tracker_reset_clears_everything():
    t = AutoTracker()
    t.record_answer()
    t.record_action("retrieve")
    t.set_complexity(0.8)
    t.reset()
    assert t.consecutive_answer_directly == 0
    assert t.recent_actions == ()
    assert t.complexity_score == 0.0


def test_tracker_empty_action_is_noop():
    t = AutoTracker()
    t.record_action("")
    t.record_action("   ")
    assert t.recent_actions == ()
    assert t.consecutive_answer_directly == 0


# ---- HostLoop -----------------------------------------------------------


def test_host_loop_folds_tracker_snapshot_into_context(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path, policy_v3_enabled=True))
    loop = HostLoop(compass)
    # Set ONLY complexity (not uncertainty) so the engine walks past
    # the EXPLORE branch (no remote), past the uncertainty branch
    # (no uncertainty), and lands on the complexity branch.
    loop.tracker.set_complexity(0.9)
    decision = loop.decide("small task", remote_allowed=False, has_sufficient_context=True)
    assert decision.action is DecisionAction.RETRIEVE_THEN_ACT
    # The tracker should now reflect the recorded suggestion.
    recent = loop.tracker.recent_actions
    assert "retrieve_then_act" in recent


def test_host_loop_uncertainty_fires_when_remote_blocked(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path, policy_v3_enabled=True))
    loop = HostLoop(compass)
    # Set ONLY uncertainty so the engine lands on the uncertainty
    # branch (which fires before complexity when EXPLORE is gated off
    # by the missing remote flag).
    loop.tracker.set_uncertainty(0.7)
    decision = loop.decide("what now", remote_allowed=False, has_sufficient_context=True)
    assert decision.action is DecisionAction.RETRIEVE
    assert "uncertainty_threshold" in decision.reason_codes


def test_host_loop_action_pressure_breaks_silence(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path, policy_v3_enabled=True))
    loop = HostLoop(compass)
    # Simulate three silent answers in a row.
    loop.tracker.record_answer()
    loop.tracker.record_answer()
    loop.tracker.record_answer()
    decision = loop.decide("what now")
    assert decision.action is DecisionAction.RETRIEVE_THEN_ACT
    assert "action_pressure" in decision.reason_codes


def test_host_loop_record_routes_to_tracker(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    loop = HostLoop(compass)
    loop.record("retrieve")
    loop.record("answer")
    loop.record("answer")
    assert loop.tracker.recent_actions == ("retrieve",)
    assert loop.tracker.consecutive_answer_directly == 2


def test_host_loop_explain_returns_state(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path, policy_v3_enabled=True))
    loop = HostLoop(compass)
    loop.record("retrieve")
    loop.tracker.set_complexity(0.4)
    snap = loop.explain()
    assert snap["policy_version"] == "policy-v3"
    assert snap["tracker"]["recent_actions"] == ["retrieve"]
    assert snap["tracker"]["complexity_score"] == 0.4
    assert snap["last_decision"] is None


def test_host_loop_per_call_complexity_override(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path, policy_v3_enabled=True, remote_allowed=True))
    loop = HostLoop(compass)
    # Stored value is low; per-call override is high. The decision
    # should fire as if complexity were 0.9, but the tracker should
    # not be mutated.
    loop.tracker.set_complexity(0.2)
    decision = loop.decide("anything", complexity=0.95, remote_allowed=True, has_sufficient_context=True)
    assert decision.action is DecisionAction.EXPLORE
    assert loop.tracker.complexity_score == 0.2  # unchanged


# ---- apply_smart_defaults ----------------------------------------------


def test_smart_defaults_enable_v3(tmp_path):
    config = CompassConfig(data_dir=tmp_path, policy_v3_enabled=False)
    compass = Compass(config)
    changes = apply_smart_defaults(compass)
    assert compass.config.policy_v3_enabled is True
    assert changes["policy_v3_enabled"] is True


def test_smart_defaults_is_a_one_shot_bootstrap(tmp_path):
    """The function is a one-shot bootstrap. A host that wants v3
    *off* can flip it back after calling, and the next call will
    *not* re-flip it: the second call is a no-op because the only
    change that would happen is the v3 gate, and the function's
    purpose is to *enable* the gate, not to keep fighting the
    user."""
    config = CompassConfig(data_dir=tmp_path, policy_v3_enabled=False)
    compass = Compass(config)
    first = apply_smart_defaults(compass)
    assert compass.config.policy_v3_enabled is True
    assert first.get("policy_v3_enabled") is True
    # Second call: nothing left to flip. Empty diff.
    second = apply_smart_defaults(compass)
    assert second == {}


def test_smart_defaults_force_overrides_explicit_opt_out(tmp_path):
    config = CompassConfig(data_dir=tmp_path, policy_v3_enabled=False)
    compass = Compass(config)
    apply_smart_defaults(compass, force=True)
    assert compass.config.policy_v3_enabled is True


def test_smart_defaults_wires_web_retriever(tmp_path):
    config = CompassConfig(data_dir=tmp_path, remote_allowed=True)
    compass = Compass(config)
    apply_smart_defaults(compass)
    names = [getattr(r, "name", "") for r in compass.retrieval.retrievers]
    assert "web_search_ddg" in names


def test_smart_defaults_skips_web_retriever_when_remote_blocked(tmp_path):
    config = CompassConfig(data_dir=tmp_path, remote_allowed=False)
    compass = Compass(config)
    apply_smart_defaults(compass)
    names = [getattr(r, "name", "") for r in compass.retrieval.retrievers]
    assert "web_search_ddg" not in names


def test_smart_defaults_is_idempotent(tmp_path):
    config = CompassConfig(data_dir=tmp_path, remote_allowed=True)
    compass = Compass(config)
    first = apply_smart_defaults(compass)
    second = apply_smart_defaults(compass)
    # Second call is a no-op — no changes, no extra web retriever.
    assert second == {}
    names = [getattr(r, "name", "") for r in compass.retrieval.retrievers]
    assert names.count("web_search_ddg") == 1


def test_build_smart_default_config_returns_v3_config():
    config = build_smart_default_config(remote_allowed=True)
    assert config.policy_v3_enabled is True
    assert config.complexity_threshold == 0.6
    assert config.uncertainty_threshold == 0.5
    assert config.action_pressure_threshold == 3
    assert config.remote_allowed is True


# ---- end-to-end "fresh host has v3 + web" ------------------------------


def test_fresh_host_uses_v3_and_web_out_of_the_box(tmp_path):
    """The headline test: a host that follows the recipe gets v3 + web
    adapter automatically, with no manual wiring."""
    from agent_compass import Compass
    from agent_compass.runtime import apply_smart_defaults

    compass = Compass(build_smart_default_config(data_dir=tmp_path, remote_allowed=True))
    apply_smart_defaults(compass)

    # 1. v3 is on.
    assert compass.config.policy_v3_enabled is True

    # 2. The web adapter is wired.
    names = [getattr(r, "name", "") for r in compass.retrieval.retrievers]
    assert "web_search_ddg" in names

    # 3. The HostLoop auto-tracks silent answers.
    loop = HostLoop(compass)
    loop.record("answer")
    loop.record("answer")
    loop.record("answer")
    decision = loop.decide("what now")
    assert decision.action is DecisionAction.RETRIEVE_THEN_ACT
    assert "action_pressure" in decision.reason_codes

    # 4. EXPLORE fires when remote is allowed and complexity is high.
    decision = loop.decide("what changed in fastapi 0.118",
                           complexity=0.9, remote_allowed=True, has_sufficient_context=True)
    assert decision.action is DecisionAction.EXPLORE


def test_host_loop_auto_injects_remote_allowed(tmp_path):
    """v0.9.1: a host that forgets the flag still gets EXPLORE when the
    compass config has remote_allowed=True. This is the friction that
    v0.9.0 dogfood surfaced and the recipe used to paper over."""
    compass = Compass(build_smart_default_config(data_dir=tmp_path, remote_allowed=True))
    apply_smart_defaults(compass)
    loop = HostLoop(compass)

    # No remote_allowed in overrides — loop should inject from config.
    decision = loop.decide("what changed in fastapi 0.118", complexity=0.9)
    assert decision.action is DecisionAction.EXPLORE, (
        f"auto-injected remote_allowed should fire EXPLORE, got {decision.action}"
    )
    assert "complexity_explore" in decision.reason_codes


def test_host_loop_caller_can_override_remote_allowed(tmp_path):
    """A host that wants to gate EXPLORE per call (e.g. on a transient
    network outage) can still pass remote_allowed=False and override
    the config-level flag."""
    compass = Compass(build_smart_default_config(data_dir=tmp_path, remote_allowed=True))
    apply_smart_defaults(compass)
    loop = HostLoop(compass)

    decision = loop.decide(
        "what changed in fastapi 0.118",
        complexity=0.9,
        remote_allowed=False,
    )
    # Override blocks EXPLORE — falls through to complexity_without_recent_retrieval.
    assert decision.action is DecisionAction.RETRIEVE_THEN_ACT, (
        f"caller override should block EXPLORE, got {decision.action}"
    )
    assert "complexity_explore" not in decision.reason_codes


def test_host_loop_does_not_inject_when_remote_blocked(tmp_path):
    """When the config says offline, EXPLORE never fires — even if the
    caller forgets to pass remote_allowed explicitly."""
    compass = Compass(build_smart_default_config(data_dir=tmp_path, remote_allowed=False))
    apply_smart_defaults(compass)
    loop = HostLoop(compass)

    decision = loop.decide("what changed in fastapi 0.118", complexity=0.9)
    assert decision.action is not DecisionAction.EXPLORE, (
        f"offline host must not get EXPLORE, got {decision.action}"
    )
