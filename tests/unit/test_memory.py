import pytest

from agent_compass.memory.scoring import score_memory, retention


def test_score_is_versioned_and_bounded_context():
    result = score_memory(access_count=3, days_elapsed=2, keyword_hits=20, memory_type="task_lesson")
    assert result.formula_version == "activation-v1"
    assert result.context == 0.75
    assert result.score > result.importance


def test_retention_decays():
    assert retention(0, 10) == 1.0
    assert retention(10, 10) < 1.0
    assert retention(20, 10) < retention(10, 10)


def test_invalid_score_inputs_are_rejected():
    with pytest.raises(ValueError):
        score_memory(access_count=-1, days_elapsed=0)
    with pytest.raises(ValueError):
        retention(-1, 10)
