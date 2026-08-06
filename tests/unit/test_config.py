import json
import textwrap

import pytest

from agent_compass.config import CompassConfig


def test_default_constructor_is_dependency_free():
    config = CompassConfig()
    assert config.schema_version == "1"
    assert config.ambiguity_threshold == pytest.approx(0.7)
    assert config.max_retries == 3
    assert "delete" in config.destructive_actions


def test_load_from_json(tmp_path):
    cfg = tmp_path / "policy.json"
    cfg.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "policy": {
                    "retrieval": {"remote_requires_explicit_or_high_confidence": False},
                    "approval": {"destructive_actions": ["purge", "rebuild"]},
                    "max_retries": 5,
                    "ambiguity_threshold": 0.55,
                },
                "privacy": {"default_classification": "sensitive"},
            }
        ),
        encoding="utf-8",
    )
    config = CompassConfig.load(cfg)
    assert config.remote_allowed is True
    assert config.destructive_actions == {"purge", "rebuild"}
    assert config.max_retries == 5
    assert config.ambiguity_threshold == pytest.approx(0.55)
    assert config.default_privacy == "sensitive"


def test_load_from_yaml_without_pyyaml(tmp_path):
    cfg = tmp_path / "policy.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            schema_version: "1"
            policy:
              retrieval:
                remote_requires_explicit_or_high_confidence: false
              approval:
                destructive_actions:
                  - purge
                  - rebuild
              max_retries: 4
              ambiguity_threshold: 0.6
            privacy:
              default_classification: sensitive
            """
        ).strip(),
        encoding="utf-8",
    )
    config = CompassConfig.load(cfg)
    assert config.remote_allowed is True
    assert config.destructive_actions == {"purge", "rebuild"}
    assert config.max_retries == 4
    assert config.ambiguity_threshold == pytest.approx(0.6)
    assert config.default_privacy == "sensitive"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        CompassConfig.load(tmp_path / "missing.yaml")
