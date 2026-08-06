from agent_compass import Compass, CompassConfig
from agent_compass.models import DecisionAction, DecisionContext, SessionState


def _compass(tmp_path, **kwargs):
    return Compass(CompassConfig(data_dir=tmp_path, **kwargs))


def test_retry_budget_exhaustion_triggers_stop(tmp_path):
    compass = _compass(tmp_path, max_retries=2)
    decision = compass.decide(
        DecisionContext(user_input="retrying", retry_count=2, failure_streak=3, last_error="boom")
    )
    assert decision.action is DecisionAction.STOP
    assert "retry_budget_exhausted" in decision.reason_codes
    assert decision.policy_version == "policy-v2"


def test_session_ending_triggers_consolidate_memory(tmp_path):
    compass = _compass(tmp_path)
    decision = compass.decide(
        DecisionContext(
            user_input="wrapping up",
            session_state=SessionState.ENDING,
            last_error="",
        )
    )
    assert decision.action is DecisionAction.CONSOLIDATE_MEMORY
    assert "session_ending" in decision.reason_codes


def test_interrupted_task_resumes(tmp_path):
    compass = _compass(tmp_path)
    decision = compass.decide(
        DecisionContext(
            user_input="continue please",
            task_in_progress=True,
            interrupted=True,
        )
    )
    assert decision.action is DecisionAction.RESUME
    assert "task_interrupted" in decision.reason_codes


def test_ongoing_task_continues_not_resumes(tmp_path):
    compass = _compass(tmp_path)
    decision = compass.decide(
        DecisionContext(user_input="keep going", task_in_progress=True, interrupted=False)
    )
    assert decision.action is DecisionAction.CONTINUE
    assert "task_in_progress" in decision.reason_codes


def test_destructive_proposed_action_requires_approval(tmp_path):
    compass = _compass(tmp_path)
    decision = compass.decide(
        DecisionContext(
            user_input="rotate the cache",
            proposed_actions=["publish_release_notes"],
        )
    )
    assert decision.action is DecisionAction.PAUSE_FOR_APPROVAL
    assert decision.reason_codes[0].startswith("destructive_action:")


def test_time_sensitive_keyword_auto_detected(tmp_path):
    compass = _compass(tmp_path)
    decision = compass.decide(DecisionContext(user_input="what is the latest version?"))
    assert decision.action is DecisionAction.RETRIEVE
    assert "time_sensitive" in decision.reason_codes
    assert any(code.startswith("time_sensitive_keyword:") for code in decision.reason_codes)


def test_ambiguity_threshold_is_configurable(tmp_path):
    compass = _compass(tmp_path, ambiguity_threshold=0.5)
    decision = compass.decide(DecisionContext(user_input="do the thing", ambiguity=0.6))
    assert decision.action is DecisionAction.ASK_USER


def test_remote_blocked_when_config_disallows(tmp_path):
    compass = _compass(tmp_path, remote_allowed=False)
    decision = compass.decide(
        DecisionContext(user_input="search the web", explicit_search_request=True, remote_allowed=True)
    )
    assert decision.action is DecisionAction.RETRIEVE
    assert decision.scope == "local"
    assert "remote_not_allowed" in decision.reason_codes
