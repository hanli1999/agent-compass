"""Tests for activation-v2 scoring and version routing."""
from __future__ import annotations

import math

import pytest
from hypothesis import given, settings, strategies as st

from agent_compass.memory.scoring import (
    DEFAULT_CONTEXT_CAP,
    DUAL_RETENTION_SWITCH_DAYS,
    EMOTION_WEIGHTS,
    FORMULA_VERSION,
    FORMULA_VERSION_V2,
    INSTINCT_WEIGHTS,
    retention,
    retention_dual,
    route_score,
    score_memory,
    score_memory_v2,
    stability_days,
)

MEMORY_TYPES = st.sampled_from(
    ["identity", "decision", "event", "preference", "task_lesson", "project_context"]
)


# --------------------------------------------------------------- dual retention

def test_dual_retention_starts_at_one():
    assert retention_dual(0.0, 30.0) == pytest.approx(1.0)


def test_dual_retention_is_continuous_at_the_switch_point():
    before = retention_dual(DUAL_RETENTION_SWITCH_DAYS, 30.0)
    after = retention_dual(DUAL_RETENTION_SWITCH_DAYS + 1e-6, 30.0)
    assert before == pytest.approx(after, abs=1e-6)


def test_dual_retention_decays_faster_than_single_in_the_short_term():
    assert retention_dual(3.0, 30.0) < retention(3.0, 30.0)


def test_dual_retention_retains_more_than_single_in_the_long_term():
    # The whole point of the two-segment curve: reconsolidated memories plateau.
    assert retention_dual(60.0, 60.0) > retention(60.0, 60.0)


@given(
    days=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False),
    stability=st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False),
)
def test_dual_retention_is_in_unit_interval(days, stability):
    assert 0.0 <= retention_dual(days, stability) <= 1.0


@given(
    a=st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
    delta=st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)
def test_dual_retention_is_monotonically_decreasing(a, delta):
    assert retention_dual(a + delta, 30.0) <= retention_dual(a, 30.0) + 1e-12


def test_retention_functions_reject_bad_inputs():
    with pytest.raises(ValueError):
        retention_dual(-1.0, 30.0)
    with pytest.raises(ValueError):
        retention_dual(1.0, 0.0)
    with pytest.raises(ValueError):
        retention_dual(1.0, 30.0, switch_point=0.0)


# ------------------------------------------------------------------ v2 formula

def test_v2_reports_its_own_formula_version():
    result = score_memory_v2(access_count=1, days_elapsed=1.0)
    assert result.formula_version == FORMULA_VERSION_V2


def test_v2_context_is_normalised_to_unit_interval():
    saturating_hits = math.ceil(DEFAULT_CONTEXT_CAP / 0.15)
    assert score_memory_v2(
        access_count=0, days_elapsed=0.0, keyword_hits=saturating_hits
    ).context == pytest.approx(1.0)
    assert score_memory_v2(access_count=0, days_elapsed=0.0, keyword_hits=0).context == 0.0


def test_v2_emotion_saturates_at_one():
    # "happy" is weighted 1.3 upstream but must not exceed the other dimensions.
    assert EMOTION_WEIGHTS["happy"] > 1.0
    result = score_memory_v2(access_count=1, days_elapsed=1.0, emotion_tag="happy")
    assert result.emotion == pytest.approx(1.0)


def test_v2_emotion_below_one_is_passed_through():
    result = score_memory_v2(access_count=1, days_elapsed=1.0, emotion_tag="sad")
    assert result.emotion == pytest.approx(EMOTION_WEIGHTS["sad"])


def test_v2_instinct_is_passed_through():
    result = score_memory_v2(access_count=1, days_elapsed=1.0, instinct_tag="survival")
    assert result.instinct == pytest.approx(INSTINCT_WEIGHTS["survival"])


def test_v2_unknown_tags_contribute_nothing_instead_of_raising():
    result = score_memory_v2(
        access_count=1, days_elapsed=1.0, emotion_tag="schadenfreude", instinct_tag="wanderlust"
    )
    assert result.emotion == 0.0
    assert result.instinct == 0.0


def test_v2_tags_never_change_the_base_component():
    plain = score_memory_v2(access_count=5, days_elapsed=3.0)
    tagged = score_memory_v2(
        access_count=5, days_elapsed=3.0, emotion_tag="happy", instinct_tag="survival"
    )
    assert tagged.base == plain.base
    assert tagged.score > plain.score


def test_v2_auxiliary_block_is_the_mean_of_four_dimensions():
    r = score_memory_v2(
        access_count=3,
        days_elapsed=2.0,
        keyword_hits=2,
        memory_type="identity",
        emotion_tag="neutral",
        instinct_tag="kin",
    )
    expected_aux = (r.context + r.importance + r.emotion + r.instinct) / 4.0
    assert r.score == pytest.approx(r.base + expected_aux, abs=1e-6)


def test_v2_can_opt_out_of_dual_retention():
    dual = score_memory_v2(access_count=10, days_elapsed=60.0, memory_type="identity")
    single = score_memory_v2(
        access_count=10, days_elapsed=60.0, memory_type="identity", use_dual_retention=False
    )
    assert dual.base > single.base


def test_v2_rejects_bad_inputs():
    with pytest.raises(ValueError):
        score_memory_v2(access_count=-1, days_elapsed=0.0)
    with pytest.raises(ValueError):
        score_memory_v2(access_count=0, days_elapsed=0.0, keyword_hits=-1)
    with pytest.raises(ValueError):
        score_memory_v2(access_count=0, days_elapsed=0.0, context_cap=0.0)


@given(
    access_count=st.integers(min_value=0, max_value=10_000),
    days=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False),
    keyword_hits=st.integers(min_value=0, max_value=200),
    memory_type=MEMORY_TYPES,
    emotion_tag=st.sampled_from([None, *EMOTION_WEIGHTS]),
    instinct_tag=st.sampled_from([None, *INSTINCT_WEIGHTS]),
)
@settings(max_examples=200)
def test_v2_components_stay_bounded(
    access_count, days, keyword_hits, memory_type, emotion_tag, instinct_tag
):
    r = score_memory_v2(
        access_count=access_count,
        days_elapsed=days,
        keyword_hits=keyword_hits,
        memory_type=memory_type,
        emotion_tag=emotion_tag,
        instinct_tag=instinct_tag,
    )
    assert r.score >= 0.0
    assert 0.0 <= r.context <= 1.0
    assert 0.0 <= r.importance <= 1.0
    assert 0.0 <= r.emotion <= 1.0
    assert 0.0 <= r.instinct <= 1.0
    # The auxiliary block is a mean of four unit-interval values, so it can
    # never outweigh a well-established memory's base activation.
    assert r.score - r.base <= 1.0 + 1e-6


@given(
    access_count=st.integers(min_value=0, max_value=1_000),
    memory_type=MEMORY_TYPES,
)
def test_v2_more_recent_memories_score_higher(access_count, memory_type):
    recent = score_memory_v2(access_count=access_count, days_elapsed=1.0, memory_type=memory_type)
    old = score_memory_v2(access_count=access_count, days_elapsed=100.0, memory_type=memory_type)
    assert recent.score >= old.score


# --------------------------------------------------------------------- routing

def test_route_defaults_to_v1():
    routed = route_score(access_count=2, days_elapsed=1.0, keyword_hits=3)
    direct = score_memory(access_count=2, days_elapsed=1.0, keyword_hits=3)
    assert routed == direct
    assert routed.formula_version == FORMULA_VERSION


def test_route_dispatches_to_v2_when_asked():
    routed = route_score(
        access_count=2,
        days_elapsed=1.0,
        keyword_hits=3,
        emotion_tag="happy",
        formula_version=FORMULA_VERSION_V2,
    )
    direct = score_memory_v2(
        access_count=2, days_elapsed=1.0, keyword_hits=3, emotion_tag="happy"
    )
    assert routed == direct


def test_route_ignores_v2_only_fields_on_v1_records():
    """A legacy row that somehow carries tags must still score exactly as v1."""
    with_tags = route_score(
        access_count=2,
        days_elapsed=1.0,
        emotion_tag="happy",
        instinct_tag="survival",
        formula_version=FORMULA_VERSION,
    )
    without = route_score(access_count=2, days_elapsed=1.0, formula_version=FORMULA_VERSION)
    assert with_tags == without


def test_unknown_formula_version_falls_back_to_v1():
    routed = route_score(access_count=2, days_elapsed=1.0, formula_version="activation-v99")
    assert routed.formula_version == FORMULA_VERSION


# ---------------------------------------------------------------- golden values

GOLDEN_V1 = {
    ("identity", 0, 0.0): 0.9,
    ("task_lesson", 10, 5.0): 2.8438,
    ("project_context", 3, 30.0): 1.2993,
}

GOLDEN_V2 = {
    ("identity", 0, 0.0): 0.225,
    ("task_lesson", 10, 5.0): 2.2496,
    ("project_context", 3, 30.0): 0.9592,
}


@pytest.mark.parametrize("key,expected", sorted(GOLDEN_V1.items()))
def test_golden_v1_scores_do_not_drift(key, expected):
    memory_type, access_count, days = key
    result = score_memory(
        access_count=access_count, days_elapsed=days, memory_type=memory_type
    )
    assert result.score == pytest.approx(expected, abs=1e-4)


@pytest.mark.parametrize("key,expected", sorted(GOLDEN_V2.items()))
def test_golden_v2_scores_do_not_drift(key, expected):
    memory_type, access_count, days = key
    result = score_memory_v2(
        access_count=access_count, days_elapsed=days, memory_type=memory_type
    )
    assert result.score == pytest.approx(expected, abs=1e-4)


def test_event_memories_barely_decay():
    """type=event exists so historical anchors survive; check it actually holds."""
    fresh = score_memory_v2(access_count=1, days_elapsed=0.0, memory_type="event")
    year_old = score_memory_v2(access_count=1, days_elapsed=365.0, memory_type="event")
    assert year_old.base > fresh.base * 0.5
    assert stability_days(1, "event") > stability_days(1, "identity")
