"""Console (ANSI color) tests."""
import os

from agent_compass import console


def test_colorize_disabled_returns_plain():
    assert console.colorize("hi", console.RED, enabled=False) == "hi"


def test_colorize_enabled_wraps_text():
    out = console.colorize("hi", console.RED)
    assert out.startswith(console.RED)
    assert out.endswith(console.RESET)
    assert "hi" in out


def test_no_color_env_disables_support(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert console.supports_color() is False


def test_force_color_env_enables_support(monkeypatch, capsys):
    # We need a non-TTY stream for the test; force_color wins even without TTY.
    monkeypatch.setenv("AGENT_COMPASS_FORCE_COLOR", "1")
    # Make NO_COLOR not set so force_color is the deciding factor.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("AGENT_COMPASS_NO_COLOR", raising=False)
    assert console.supports_color() is True


def test_supports_color_respects_force(monkeypatch):
    """The function consults both env vars; FORCE_COLOR short-circuits to True."""
    import inspect

    src = inspect.getsource(console.supports_color)
    assert "AGENT_COMPASS_FORCE_COLOR" in src
    assert "NO_COLOR" in src


def test_level_and_action_color_palettes():
    assert console.level_color("secret") == console.RED
    assert console.level_color("public") == console.GREEN
    assert console.action_color("stop") == console.RED
    assert console.action_color("answer_directly") == console.GREEN
    assert console.level_color("nonsense") == ""


def test_horizontal_rule_length():
    assert len(console.horizontal_rule(8)) >= 8
    assert console.horizontal_rule(8, enabled=False).count("─") == 8
