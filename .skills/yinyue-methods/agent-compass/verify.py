"""Headline verification test for the agent-compass host SDK.

This is the same test that lives in
`tests/unit/test_runtime.py::test_fresh_host_uses_v3_and_web_out_of_the_box`,
shipped as a stand-alone script so any adopter can run it against their
install without checking out the full test suite.

Usage:
    pip install agent-compass
    python verify.py

Exit code 0 means the SDK is alive: v3 is on, a web adapter is wired,
silent-thinking loops break, and complex remote tasks fire EXPLORE.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Allow `python verify.py` from the repo root.
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from agent_compass import Compass  # noqa: E402
from agent_compass.models import DecisionAction  # noqa: E402
from agent_compass.runtime import (  # noqa: E402
    HostLoop,
    apply_smart_defaults,
    build_smart_default_config,
)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        compass = Compass(build_smart_default_config(data_dir=data_dir, remote_allowed=True))
        apply_smart_defaults(compass)

        assert compass.config.policy_v3_enabled is True, "v3 should be on"
        names = [getattr(r, "name", "") for r in compass.retrieval.retrievers]
        assert "web_search_ddg" in names, f"DDG adapter should be wired, got {names}"

        loop = HostLoop(compass)
        loop.record("answer")
        loop.record("answer")
        loop.record("answer")
        decision = loop.decide("what now")
        assert decision.action is DecisionAction.RETRIEVE_THEN_ACT, (
            f"silent-thinking loop should break, got {decision.action}"
        )
        assert "action_pressure" in decision.reason_codes, (
            f"action_pressure should be a reason, got {decision.reason_codes}"
        )

        decision = loop.decide(
            "what changed in fastapi 0.118",
            complexity=0.9,
            remote_allowed=True,
            has_sufficient_context=True,
        )
        assert decision.action is DecisionAction.EXPLORE, (
            f"complex remote task should fire EXPLORE, got {decision.action}"
        )

    print("OK — agent-compass SDK is alive")
    print("  - v3 enabled")
    print("  - DuckDuckGoAdapter wired")
    print("  - silent-thinking loop broken by action_pressure")
    print("  - EXPLORE fires on complex remote task")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL — {e}", file=sys.stderr)
        raise SystemExit(1)