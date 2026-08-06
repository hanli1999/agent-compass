import pytest

from agent_compass import Compass, CompassConfig
from agent_compass.models import MemoryStatus


def _compass(tmp_path):
    return Compass(CompassConfig(data_dir=tmp_path))


def test_propose_persists_candidate_with_score(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose(
        "always run unit tests before merging",
        memory_type="task_lesson",
        keywords=["test", "merge"],
    )
    assert memory.status is MemoryStatus.CANDIDATE
    assert memory.score is not None
    stored = compass.memory.list()
    assert len(stored) == 1
    assert stored[0]["memory_id"] == memory.memory_id


def test_propose_blocks_secret_content(tmp_path):
    compass = _compass(tmp_path)
    with pytest.raises(ValueError):
        compass.memory.propose("api_key=abcdefghijklmnop")


def test_lifecycle_candidate_to_active(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose("prefer offline test runs")
    compass.memory.accept(memory.memory_id)
    activated = compass.memory.activate(memory.memory_id)
    assert activated.status is MemoryStatus.ACTIVE


def test_invalid_transition_rejected(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose("rule")
    with pytest.raises(ValueError):
        compass.memory.activate(memory.memory_id)


def test_touch_increments_access_count_and_score(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose("use deterministic test data", memory_type="task_lesson")
    touched = compass.memory.touch(memory.memory_id)
    assert touched.access_count == 1
    assert touched.last_accessed is not None
    assert touched.score is not None


def test_prune_demotes_low_score_memories(tmp_path):
    compass = _compass(tmp_path)
    compass.memory.propose("never do this", memory_type="temporary_note", importance=0.0)
    summary = compass.memory.prune(below=0.5, stale_below=0.6, dry_run=False)
    assert summary["archived"] >= 1
    remaining = compass.memory.list()
    statuses = {item["status"] for item in remaining}
    assert MemoryStatus.ARCHIVED.value in statuses or len(remaining) == 0


def test_delete_removes_memory(tmp_path):
    compass = _compass(tmp_path)
    memory = compass.memory.propose("throwaway note")
    assert compass.memory.delete(memory.memory_id) is True
    assert compass.memory.list() == []
