from agent_compass import Compass, CompassConfig
from agent_compass.privacy.boundary import PrivacyLevel


def test_secret_is_blocked(tmp_path):
    boundary = Compass(CompassConfig(data_dir=tmp_path)).privacy
    result = boundary.inspect("Authorization: Bearer abcdefghijklmnop")
    assert result.level is PrivacyLevel.SECRET
    assert result.blocked
    assert "[REDACTED:bearer]" in boundary.redact("Authorization: Bearer abcdefghijklmnop")


def test_sensitive_is_redacted_not_blocked(tmp_path):
    boundary = Compass(CompassConfig(data_dir=tmp_path)).privacy
    result = boundary.inspect("contact alice@example.com")
    assert result.level is PrivacyLevel.SENSITIVE
    assert not result.blocked
    assert "[REDACTED:email]" in boundary.assert_safe_for_remote("contact alice@example.com")


def test_memory_cannot_capture_secret(tmp_path):
    compass = Compass(CompassConfig(data_dir=tmp_path))
    try:
        compass.memory.propose("api_key=abcdefghijklmnop")
    except ValueError as exc:
        assert "secret" in str(exc)
    else:
        raise AssertionError("secret memory proposal was accepted")
