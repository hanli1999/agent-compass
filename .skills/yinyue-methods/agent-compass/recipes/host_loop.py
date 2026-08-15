"""The four-line host loop recipe — every DecisionAction branch handled.

Run this verbatim against any agent-compass install to confirm the
host-side SDK is alive. Adapt the `speak` / `ask_user` / `wait_for_approval`
helpers to your runtime.

    pip install agent-compass
    python recipes/host_loop.py --input "what now"

This file is *self-checking*: it runs the headline verification test
before printing the decision. If verification fails it raises so CI
sees the breakage.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Allow `python recipes/host_loop.py` from the repo root.
ROOT = Path(__file__).resolve().parents[4]
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


def verify() -> None:
    """Headline test — fresh host has v3 + web out of the box."""
    with tempfile.TemporaryDirectory() as tmp:
        compass = Compass(build_smart_default_config(data_dir=Path(tmp), remote_allowed=True))
        apply_smart_defaults(compass)
        assert compass.config.policy_v3_enabled is True, "v3 should be on"
        names = [getattr(r, "name", "") for r in compass.retrieval.retrievers]
        assert "web_search_ddg" in names, "DDG adapter should be wired"

        loop = HostLoop(compass)
        loop.record("answer")
        loop.record("answer")
        loop.record("answer")
        decision = loop.decide("what now")
        assert decision.action is DecisionAction.RETRIEVE_THEN_ACT, (
            f"silent-thinking loop should break, got {decision.action}"
        )
        assert "action_pressure" in decision.reason_codes

        decision = loop.decide(
            "what changed in fastapi 0.118",
            complexity=0.9,
            remote_allowed=True,
            has_sufficient_context=True,
        )
        assert decision.action is DecisionAction.EXPLORE, (
            f"complex remote task should fire EXPLORE, got {decision.action}"
        )


def build_loop(data_dir: Path | None = None, remote_allowed: bool = True) -> HostLoop:
    """Build the four-line host loop with smart defaults applied."""
    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="agent-compass-"))
    compass = Compass(build_smart_default_config(data_dir=data_dir, remote_allowed=remote_allowed))
    apply_smart_defaults(compass)
    return HostLoop(compass)


def on_tool_call(loop: HostLoop, name: str) -> None:
    """Call this after every tool invocation."""
    loop.record(name)


def speak(text: str) -> None:
    """Wire to your runtime. Default: print to stdout."""
    print(text)


def ask_user(prompt: str) -> None:
    """Wire to your runtime. Default: print the prompt."""
    print(f"ASK: {prompt}")


def wait_for_approval(reason: str) -> None:
    """Wire to your runtime. Default: print the reason."""
    print(f"PAUSE: {reason}")


def recall_and_speak(compass: Compass, prompt: str) -> None:
    """Pull summaries from local memory and speak. Bounded digest."""
    result = compass.recall(prompt, token_budget=800)
    if result.truncated:
        speak(f"(showing top {len(result.items)}, "
              f"{result.dropped_for_limit + result.dropped_for_budget} more not shown)")
    for item in result:
        speak(item.summary)


def on_user_prompt(loop: HostLoop, compass: Compass, prompt: str, **overrides) -> None:
    """The host's main entry point. Branches on DecisionAction.

    As of v0.9.1, ``HostLoop.decide()`` auto-injects
    ``compass.config.remote_allowed`` into the ``DecisionContext`` so
    the host does not have to thread the flag by hand. A caller that
    wants to gate ``EXPLORE`` per call (e.g. on a transient network
    outage) can still pass ``remote_allowed=False`` and override the
    config-level flag.
    """
    decision = loop.decide(prompt, **overrides)
    action = decision.action

    if action is DecisionAction.ANSWER_DIRECTLY:
        speak("answer_directly")
    elif action is DecisionAction.RETRIEVE:
        recall_and_speak(compass, prompt)
    elif action is DecisionAction.RETRIEVE_THEN_ACT:
        recall_and_speak(compass, prompt)
        speak("(retrieve_then_act — at least one tool step before final answer)")
    elif action is DecisionAction.EXPLORE:
        speak("explore — web_search → inspect → maybe web_fetch → answer")
    elif action is DecisionAction.ASK_USER:
        ask_user(prompt)
    elif action is DecisionAction.PAUSE_FOR_APPROVAL:
        wait_for_approval(prompt)
    elif action is DecisionAction.STOP:
        speak("stop — retry budget exhausted")
    elif action is DecisionAction.RESUME:
        speak("resume — pick up from the last checkpoint")
    elif action is DecisionAction.CONSOLIDATE_MEMORY:
        speak("consolidate_memory — flush learnings")
    elif action is DecisionAction.CONTINUE:
        speak("continue — keep going")
    else:
        speak(f"unhandled action: {action}")


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-compass host loop recipe")
    parser.add_argument("--input", default="what now", help="the user prompt to decide on")
    parser.add_argument("--complexity", type=float, default=None)
    parser.add_argument("--uncertainty", type=float, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--no-remote", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    args = parser.parse_args()

    if not args.skip_verify:
        verify()
        print("verify: OK", file=sys.stderr)

    loop = build_loop(data_dir=args.data_dir, remote_allowed=not args.no_remote)
    on_user_prompt(
        loop,
        loop.compass,
        args.input,
        complexity=args.complexity,
        uncertainty=args.uncertainty,
    )
    print("\nfinal explain:", loop.explain(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())