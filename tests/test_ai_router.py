import pytest

import core.ai.ai_router as ai_router
from core.ai.ai_router import AllProvidersFailed


@pytest.fixture(autouse=True)
def _stub_telegram_outbound(monkeypatch):
    """Prevent real Telegram sends from failover notifications during tests.

    _send_failover_notification() fires send_telegram_alert() -> send_message()
    whenever delegate() fails over between providers. In the test environment
    the bot token is present in .env, so an unstubbed send would POST a real
    message (or hang on a 15s timeout). A no-op stub keeps every delegate()
    failover test fast and side-effect-free. Tests that assert on notification
    content patch send_message themselves inside the test body.
    """
    import core.telegram_bridge as telegram_bridge
    monkeypatch.setattr(
        telegram_bridge, "send_message",
        lambda text, token=None, chat_id=None, reply_markup=None: None,
    )


@pytest.mark.parametrize("description,expected_type", [
    ("Design an application architecture", "planning"),
    ("Build authentication system", "coding"),
    ("Analyze Docker error log", "log_analysis"),
    ("Generate README documentation", "documentation"),
])
def test_classify_task_matches_expected_category(description, expected_type):
    assert ai_router.classify_task(description) == expected_type


def test_classify_task_falls_back_to_coding_for_unrecognized_text():
    assert ai_router.classify_task("xyzzy plugh frobnicate") == "coding"


@pytest.mark.parametrize("description,expected_provider", [
    # 2026-09-10: local-only routing — kai_brain leads planning, local leads
    # log_analysis, kai_coder leads documentation.
    ("Design an application architecture", "kai_brain"),
    ("Analyze Docker error log", "local"),
    ("Generate README documentation", "kai_coder"),
])
def test_delegate_routes_to_expected_provider(monkeypatch, description, expected_provider):
    import core.ai_provider as ai_provider

    # conftest's disable_slow_local_providers fixture already marks every local
    # provider unavailable; re-enable only the expected one and stub its
    # run_text_task so the delegate call is forced to hit it.
    provider = ai_provider.get_provider(expected_provider)
    monkeypatch.setitem(provider, "available_fn", lambda: True)
    monkeypatch.setitem(
        provider, "run_text_task",
        lambda p, timeout=60, project_path=None, n=expected_provider: f"response from {n}",
    )

    result = ai_router.delegate(description)

    assert result["provider"] == expected_provider


def test_delegate_falls_back_when_first_choice_unavailable(monkeypatch):
    import core.ai_provider as ai_provider

    # planning = ["kai_brain", "local"] -- disable the primary so the
    # local fallback answers.
    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: False)

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task", lambda p, timeout=60, project_path=None: "local answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "local"


def test_delegate_falls_back_when_first_choice_call_raises(monkeypatch):
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("kai_brain failed")

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task", boom)

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task", lambda p, timeout=60, project_path=None: "local answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "local"


def test_delegate_raises_when_every_candidate_fails(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ai_router.ROLE_PROVIDERS["planning"]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    with pytest.raises(AllProvidersFailed):
        ai_router.delegate("Design an application architecture")


def test_delegate_skips_a_candidate_known_to_be_quota_exceeded_without_calling_it(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    provider_health.capture_quota_exceeded("kai_brain", detail="daily quota exhausted")

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(
        brain, "run_text_task",
        lambda p, timeout=60, project_path=None: pytest.fail("kai_brain should have been skipped, not called"),
    )

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task", lambda p, timeout=60, project_path=None: "local answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "local"


def test_delegate_still_tries_a_candidate_with_only_a_recorded_error_not_quota_exceeded(monkeypatch):
    # provider_health deliberately never equates a raw "error" status with
    # confirmed quota exhaustion (it can't tell a transient network blip
    # from a real limit) -- only a verified quota_exceeded status should
    # cause delegate() to skip a call outright.
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    provider_health.capture_provider_error("kai_brain", detail="ConnectionError")

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task", lambda p, timeout=60, project_path=None: "kai_brain recovered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "kai_brain"


def test_delegate_records_usage_on_success(monkeypatch):
    import core.ai_provider as ai_provider

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    history = ai_router.get_usage_history()
    assert len(history) == 1
    assert history[0]["provider"] == "kai_brain"
    assert history[0]["success"] is True
    assert history[0]["task_type"] == "planning"


def test_delegate_records_usage_on_failure_too(monkeypatch):
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("boom")

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task", boom)

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task", lambda p, timeout=60, project_path=None: "ok")

    ai_router.delegate("Design an application architecture")

    history = ai_router.get_usage_history()
    assert len(history) == 2
    assert history[0]["provider"] == "kai_brain"
    assert history[0]["success"] is False
    assert history[1]["provider"] == "local"
    assert history[1]["success"] is True


def test_delegate_with_coding_agent_capability_calls_run_coding_task_not_run_text_task(monkeypatch):
    import core.ai_provider as ai_provider

    # The aim is to verify that capability=coding_agent calls run_coding_task,
    # not run_text_task, regardless of which provider answers.
    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder"]},
    )

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_text_task",
        lambda *a, **k: pytest.fail("run_text_task should not be called for capability=coding_agent"),
    )

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["project_path"] = project_path
        captured["instruction"] = instruction
        return {"success": True, "response_text": "done", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setitem(coder, "run_coding_task", fake_run_coding_task)

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    assert result["provider"] == "kai_coder"
    assert captured["project_path"] == "/proj"
    assert captured["instruction"] == "Implement the widget"
    assert result["response"]["success"] is True


def test_delegate_with_coding_agent_capability_falls_back_when_primary_fails(monkeypatch):
    import core.ai_provider as ai_provider

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": False, "response_text": "", "files_changed": [], "commits": [], "tool_errors": [{"tool": None, "content": "boom"}]},
    )

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(
        fallback, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": ["a.py"], "commits": [], "tool_errors": []},
    )

    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]})

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    # The primary's call "succeeded" at the transport level (no exception) but
    # the task itself failed -- delegate()'s coding_agent path must fall through
    # to the next candidate on a result-level failure, not just an exception,
    # since a failed generation is exactly the case that must not stall Kai.
    assert result["provider"] == "kai_brain"
    assert result["response"]["files_changed"] == ["a.py"]


def test_delegate_records_a_confirmed_usage_limit_message_as_quota_exceeded(monkeypatch):
    # A usage-limit message mid-generation is unambiguous and durable, not
    # transient -- delegate() must not keep retrying the provider every cycle.
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "You've hit your weekly limit · resets Jul 29, 1pm"}],
        },
    )

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(
        fallback, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("kai_coder")
    assert snapshot["status"] == "quota_exceeded"
    assert "weekly limit" in snapshot["detail"].lower()


def test_delegate_records_a_generic_coding_failure_as_error_not_quota_exceeded(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": "Bash", "content": "tests failed"}],
        },
    )

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(
        fallback, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("kai_coder")
    assert snapshot["status"] == "error"


def test_delegate_records_fallback_credit_exhaustion_as_quota_exceeded_and_notifies(monkeypatch):
    # Verifies that credit-exhausted coding providers are captured as
    # quota_exceeded in provider_health, so the scheduler can alert and
    # subsequent calls skip the provider.
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    primary = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(
        primary, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "Error: insufficient credit balance"}],
        },
    )

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(
        fallback, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("kai_coder")
    assert snapshot["status"] == "quota_exceeded"
    assert "insufficient credit" in snapshot["detail"].lower()


def test_fallback_quota_notify_failure_does_not_break_delegate(monkeypatch):
    # A Telegram outage must never surface as a build/generation failure.
    import core.ai_provider as ai_provider
    import core.telegram_bridge as telegram_bridge

    def _boom(text, **kwargs):
        raise RuntimeError("Telegram sendMessage failed")

    monkeypatch.setattr(telegram_bridge, "send_message", _boom)

    primary = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(
        primary, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "Error: insufficient credit balance"}],
        },
    )

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(
        fallback, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]})

    result = ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    assert result["response"]["success"] is True


def test_delegate_accepts_explicit_task_type_override(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("local"), "available_fn", lambda: False)

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task", lambda p, timeout=60, project_path=None: "forced")

    result = ai_router.delegate("some ambiguous text", task_type="log_analysis")

    assert result["provider"] == "kai_brain"
    assert result["task_type"] == "log_analysis"


def test_delegate_review_task_type_routes_to_primary(monkeypatch):
    import core.ai_provider as ai_provider

    # Disable all review candidates except kai_brain (first)
    review = ai_router.ROLE_PROVIDERS["review"]
    for name in review[1:]:
        p = ai_provider.get_provider(name)
        if p:
            monkeypatch.setitem(p, "available_fn", lambda: False)

    primary = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", lambda p, timeout=60, project_path=None: "reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "kai_brain"


def test_delegate_review_task_type_falls_back_to_last_resort(monkeypatch):
    import core.ai_provider as ai_provider

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: False)

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task", lambda p, timeout=60, project_path=None: "local reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "local"


def test_get_provider_dashboard_summarizes_last_request_per_provider(monkeypatch):
    import core.ai_provider as ai_provider

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "enabled", True)
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    dashboard = ai_router.get_provider_dashboard()

    assert "kai_brain" in dashboard
    assert dashboard["kai_brain"]["status"] == "connected"
    assert dashboard["kai_brain"]["last_task_type"] == "planning"
    assert dashboard["kai_brain"]["last_success"] is True
    assert dashboard["kai_brain"]["last_response_time_ms"] is not None


def test_get_provider_dashboard_shows_not_configured_for_unavailable_providers():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["local"]["status"] == "not_configured"


def test_get_provider_dashboard_includes_quota_percent_when_known():
    import core.ai.provider_health as provider_health

    provider_health.record_quota_snapshot("kai_brain", status="ok", percent_remaining=87.5)

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["kai_brain"]["percent_remaining"] == 87.5


def test_get_provider_dashboard_shows_none_percent_when_quota_never_checked():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["kai_brain"]["percent_remaining"] is None


def test_delegate_rotates_starting_candidate_across_successive_calls(monkeypatch):
    # The rotation mechanism: every candidate gets a turn as the first attempt.
    # Use a controlled chain with a task type NOT in FIXED_ORDER so rotation
    # actually fires (most text roles are FIXED_ORDER).
    import core.ai_provider as ai_provider

    test_chain = ["local", "llama3", "kai_brain", "kai_coder"]
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "test_rotation": test_chain,
    })

    for name in test_chain:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    seen = [ai_router.delegate("Critique this design", task_type="test_rotation")["provider"] for _ in range(len(test_chain) + 1)]

    assert seen == test_chain + [test_chain[0]]


def test_delegate_planning_always_tries_same_primary_first_not_rotated(monkeypatch):
    # Regression: "planning" is FIXED_ORDER (like "architecture") -- rotation
    # was silently giving every candidate an equal first-try turn, undermining
    # whatever ordering rationale is currently in force. Originally gemini
    # led on real success-rate evidence; 2026-08-02 operator directive
    # delegated the primary slot to deepseek_native_flash while gemini sits
    # quota_exceeded (see ROLE_PROVIDERS["planning"]'s own comment) -- this
    # test asserts FIXED_ORDER behavior itself (always the same first
    # candidate, never rotated), not a specific provider's evidence lead.
    # Unlike test_delegate_rotates_starting_candidate_across_successive_calls
    # (task_type="review", which DOES rotate), every one of these repeated
    # calls must land on the same first candidate.
    import core.ai_provider as ai_provider

    primary = ai_router.ROLE_PROVIDERS["planning"][0]

    for name in ai_router.ROLE_PROVIDERS["planning"]:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    seen = [ai_router.delegate("Plan this feature", task_type="planning")["provider"] for _ in range(4)]

    assert seen == [primary] * 4


def test_delegate_rotation_is_tracked_independently_per_task_type(monkeypatch):
    import core.ai_provider as ai_provider

    # Use controlled chains for deterministic rotation testing.
    # Use task types NOT in FIXED_ORDER so rotation actually fires
    # (most text roles are FIXED_ORDER).
    review_chain = ["local", "llama3", "kai_brain", "kai_coder"]
    log_chain = ["kai_deep", "local_brain_fast", "local_coder"]
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "test_review_rotation": review_chain,
        "test_log_rotation": log_chain,
    })

    for name in set(review_chain + log_chain):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    first = ai_router.delegate("Critique this design", task_type="test_review_rotation")["provider"]
    log_result = ai_router.delegate("Check the logs", task_type="test_log_rotation")["provider"]
    second = ai_router.delegate("Critique this design", task_type="test_review_rotation")["provider"]

    assert [first, second] == [review_chain[0], review_chain[1]]
    assert log_result == log_chain[0]


def test_delegate_rotation_still_falls_through_to_next_candidate_on_failure(monkeypatch):
    # Use a controlled chain with a task type NOT in FIXED_ORDER so the
    # fallback-through-rotation mechanism fires for this test.
    # local (first) -> kai_brain (fails) -> kai_coder (fallback)
    import core.ai_provider as ai_provider

    test_chain = ["local", "kai_brain", "kai_coder"]
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "test_rot_fallback": test_chain,
    })

    first_primary = ai_provider.get_provider("local")
    monkeypatch.setitem(first_primary, "available_fn", lambda: True)
    monkeypatch.setitem(first_primary, "run_text_task", lambda p, timeout=60, project_path=None: "from local")

    second_primary = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(second_primary, "available_fn", lambda: True)

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("kai_brain down")

    monkeypatch.setitem(second_primary, "run_text_task", boom)

    fallback = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task", lambda p, timeout=60, project_path=None: "from kai_coder")

    first = ai_router.delegate("Critique this design", task_type="test_rot_fallback")["provider"]
    second = ai_router.delegate("Critique this design", task_type="test_rot_fallback")["provider"]

    assert [first, second] == ["local", "kai_coder"]


def test_rotate_candidates_is_atomic_under_concurrent_calls():
    # 13R: with builds dispatched concurrently, two simultaneous
    # _rotate_candidates calls must never read the same index and land on
    # the same starting provider. Every concurrent call must get a distinct
    # rotation slot (the whole read-increment-write is one flock section).
    import threading

    candidates = ["a", "b", "c", "d", "e"]
    starts = []
    lock = threading.Lock()

    def rotate():
        rotated = ai_router._rotate_candidates("coding", candidates)
        with lock:
            starts.append(rotated[0])

    threads = [threading.Thread(target=rotate) for _ in range(len(candidates))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5 concurrent calls over 5 candidates: each starting provider exactly once.
    assert sorted(starts) == sorted(candidates)


def test_rotate_candidates_goes_through_memory_update(monkeypatch):
    calls = {}

    def fake_update(name, mutate_fn, directory=None):
        calls["name"] = name
        state = mutate_fn({})
        calls["state"] = state
        return state

    monkeypatch.setattr(ai_router, "update", fake_update)

    rotated = ai_router._rotate_candidates("coding", ["a", "b", "c"])

    assert calls["name"] == ai_router.ROTATION_STATE_FILE
    assert calls["state"] == {"coding": 1}
    assert rotated == ["a", "b", "c"]


# --- 13W: real per-call cost capture + workforce analytics aggregation ------

def test_record_usage_stores_a_provider_reported_cost():
    entry = ai_router.record_usage("kai_coder", "coding", "build x", success=True, duration_ms=1200, cost=0.0139422)

    assert entry["cost"] == 0.0139422
    assert ai_router.get_usage_history()[-1]["cost"] == 0.0139422


def test_record_usage_defaults_cost_to_null_not_an_estimate():
    entry = ai_router.record_usage("kai_brain", "planning", "plan x", success=True, duration_ms=800)

    assert entry["cost"] is None


def test_delegate_coding_agent_records_the_cost_reported_by_the_provider(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder"]},
    )

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True, "response_text": "ok", "files_changed": [], "commits": [],
            "tool_errors": [], "cost": 0.0139422,
        },
    )

    ai_router.delegate("Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent")

    history = ai_router.get_usage_history()
    assert history[-1]["success"] is True
    assert history[-1]["cost"] == 0.0139422


def test_delegate_records_null_cost_when_the_response_carries_none(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder"]},
    )

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": [],
        },
    )

    ai_router.delegate("Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent")

    assert ai_router.get_usage_history()[-1]["cost"] is None


def test_delegate_text_task_records_null_cost(monkeypatch):
    # Plain chat-completion providers return a string -- no cost figure to
    # capture, so the entry must record null, never a token-count estimate.
    import core.ai_provider as ai_provider

    primary = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture", task_type="planning")

    assert ai_router.get_usage_history()[-1]["cost"] is None


def test_delegate_records_cost_even_for_a_result_level_failure(monkeypatch):
    # A failed generation still incurred the cost the provider billed for it.
    import core.ai_provider as ai_provider

    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]})

    coder = ai_provider.get_provider("kai_coder")
    monkeypatch.setitem(coder, "enabled", True)
    monkeypatch.setitem(coder, "available_fn", lambda: True)
    monkeypatch.setitem(
        coder, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": "Bash", "content": "tests failed"}], "cost": 0.002,
        },
    )

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(
        fallback, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    history = ai_router.get_usage_history()
    failed = next(e for e in history if e["provider"] == "kai_coder")
    assert failed["success"] is False
    assert failed["cost"] == 0.002


def test_get_provider_dashboard_aggregates_cost_totals_and_average_duration(monkeypatch):
    monkeypatch.setattr(
        ai_router, "get_usage_history",
        lambda: [
            {"provider": "kai_coder", "success": True, "timestamp": "2026-07-30T00:00:00",
             "task_type": "coding", "duration_ms": 100, "cost": 0.01},
            {"provider": "kai_coder", "success": False, "timestamp": "2026-07-30T00:01:00",
             "task_type": "coding", "duration_ms": 300, "cost": 0.02},
            # a pre-13W entry with no cost key at all must not break the sum
            {"provider": "kai_coder", "success": True, "timestamp": "2026-07-30T00:02:00",
             "task_type": "coding", "duration_ms": 200},
        ],
    )

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["kai_coder"]["total_cost"] == pytest.approx(0.03)
    assert dashboard["kai_coder"]["cost_reported_calls"] == 2
    assert dashboard["kai_coder"]["average_duration_ms"] == pytest.approx(200.0)
    assert dashboard["kai_coder"]["total_attempts"] == 3
    assert dashboard["kai_coder"]["total_successes"] == 2


def test_get_provider_dashboard_total_cost_is_null_when_no_call_ever_reported_one(monkeypatch):
    # "no cost data" must stay distinguishable from "cost zero" -- never
    # display a fabricated 0.0 for a provider that doesn't report cost.
    monkeypatch.setattr(
        ai_router, "get_usage_history",
        lambda: [{"provider": "kai_brain", "success": True, "timestamp": "2026-07-30T00:00:00",
                  "task_type": "planning", "duration_ms": 500, "cost": None}],
    )

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["kai_brain"]["total_cost"] is None
    assert dashboard["kai_brain"]["cost_reported_calls"] == 0
    assert dashboard["kai_brain"]["average_duration_ms"] == 500


def test_get_provider_dashboard_shows_null_aggregates_for_a_provider_with_no_history():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["local"]["total_cost"] is None
    assert dashboard["local"]["average_duration_ms"] is None


def test_get_provider_dashboard_surfaces_each_providers_cost_tier():
    import core.ai_provider as ai_provider

    dashboard = ai_router.get_provider_dashboard()

    for name, entry in dashboard.items():
        assert entry["cost_tier"] in ai_provider.COST_TIERS, name

    assert dashboard["local"]["cost_tier"] == "free"
    assert dashboard["kai_coder"]["cost_tier"] == "free"


# --- 13T: evidence-based minimax routing ------------------------------------

TEXT_TASK_ROLES = ("planning", "log_analysis", "documentation", "review")


@pytest.mark.parametrize("role", TEXT_TASK_ROLES)
def test_minimax_is_not_in_any_text_task_role(role):
    # 2026-09-10: minimax removed entirely from the local-only provider set.
    # It must not appear in any text-task role.
    assert "minimax" not in ai_router.ROLE_PROVIDERS[role]


def test_every_coding_candidate_supports_the_coding_agent_capability():
    # A candidate without run_coding_task can only ever contribute a
    # "does not support coding_agent" failure string -- adding one to this
    # list would silently shorten the real fallback chain.
    import core.ai_provider as ai_provider

    for name in ai_router.ROLE_PROVIDERS["coding"]:
        provider = ai_provider.get_provider(name)
        assert provider is not None, name
        assert provider.get("run_coding_task") is not None, name


@pytest.mark.parametrize("role", TEXT_TASK_ROLES)
def test_every_text_role_candidate_supports_the_text_task_capability(role):
    import core.ai_provider as ai_provider

    for name in ai_router.ROLE_PROVIDERS[role]:
        provider = ai_provider.get_provider(name)
        assert provider is not None, name
        assert provider.get("run_text_task") is not None, name


# --- 13M: coding front rotation --------------------------------------------
# 2026-09-10: local-only provider set. CODING_ROTATING_FRONT is empty (the
# rotating front group held cloud routes that no longer exist); the coding
# chain is fixed order (kai_coder -> kai_brain), so front rotation is a no-op.


def test_candidates_for_coding_rotates_only_the_front_group():
    # Empty front group -> candidates are exactly ROLE_PROVIDERS
    # (front/tail split is by CODING_ROTATING_FRONT membership, so the tail
    # here is the full list).
    candidates = ai_router._candidates_for("coding")

    assert len(candidates) == len(ai_router.ROLE_PROVIDERS["coding"])
    assert candidates[:len(ai_router.CODING_ROTATING_FRONT)] == ai_router.CODING_ROTATING_FRONT
    assert candidates[len(ai_router.CODING_ROTATING_FRONT):] == ai_router.ROLE_PROVIDERS["coding"]


def test_candidates_for_coding_front_order_rotates_while_the_tail_never_changes():
    # With an EMPTY front group, "rotation" is a no-op and the whole list is
    # the stable tail. Note the tail is the full ROLE_PROVIDERS["coding"]
    # list in this state (the front/tail split filters by CODING_ROTATING_FRONT
    # membership).
    fronts, tails = [], []
    for _ in range(4):
        candidates = ai_router._candidates_for("coding")
        fronts.append(candidates[:len(ai_router.CODING_ROTATING_FRONT)])
        tails.append(candidates[len(ai_router.CODING_ROTATING_FRONT):])

    front = ai_router.CODING_ROTATING_FRONT
    assert all(f == front for f in fronts)
    expected_tail = [n for n in ai_router.ROLE_PROVIDERS["coding"]]
    assert all(tail == expected_tail for tail in tails)


def test_candidates_for_coding_respects_an_overridden_role_list(monkeypatch):
    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["kai_coder", "kai_brain"]}
    )

    # With no rotating-front members present, the overridden list is used
    # verbatim (and repeatedly -- nothing rotates).
    assert ai_router._candidates_for("coding") == ["kai_coder", "kai_brain"]
    assert ai_router._candidates_for("coding") == ["kai_coder", "kai_brain"]


def test_candidates_for_non_coding_roles_is_unchanged_and_unrotated():
    # 2026-08-09: planning, architecture, review, and law_* roles are now in
    # FIXED_ORDER_TASK_TYPES (they were already, unchanged by the deepseek-primary
    # change). Log_analysis and documentation are NOT fixed-order — they go through
    # performance-weighted sorting. Skip those in this comparison.
    # 2026-08-12: Compare against get_effective_providers(), not ROLE_PROVIDERS
    # directly — operator overrides (PROVIDER_CONFIG_OVERRIDES) take precedence
    # and may prepend entries like local/llama3.
    fixed_roles = ("architecture", "planning")
    for role in fixed_roles:
        assert ai_router._candidates_for(role) == ai_router.get_effective_providers(role)


def test_delegate_does_not_double_rotate_the_coding_candidates(monkeypatch):
    import core.ai_provider as ai_provider

    rotate_calls = []
    real_rotate = ai_router._rotate_candidates

    def spying_rotate(task_type, candidates):
        rotate_calls.append(list(candidates))
        return real_rotate(task_type, candidates)

    monkeypatch.setattr(ai_router, "_rotate_candidates", spying_rotate)

    # Disable all except the last one (kai_brain).
    for name in ai_router.ROLE_PROVIDERS["coding"][:-1]:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: False)

    last = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(
        last, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    # Exactly one rotation -- the front group inside _candidates_for. The
    # outer per-role rotation in delegate() must not wrap it a second time.
    assert rotate_calls == [ai_router.CODING_ROTATING_FRONT]


def test_delegate_coding_raises_all_providers_failed_when_every_candidate_is_down(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ai_router.ROLE_PROVIDERS["coding"]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    with pytest.raises(AllProvidersFailed) as excinfo:
        ai_router.delegate("Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent")

    attempted = [a["provider"] for a in excinfo.value.attempts]
    assert sorted(attempted) == sorted(ai_router.ROLE_PROVIDERS["coding"])


@pytest.mark.parametrize("role", ["planning", "log_analysis", "documentation", "review"])
def test_non_coding_roles_do_not_include_the_openrouter_coding_routes(role):
    assert "openrouter_claude_opus" not in ai_router.ROLE_PROVIDERS[role]
    assert "openrouter_claude_sonnet" not in ai_router.ROLE_PROVIDERS[role]


def test_13v_architecture_chain_candidates_all_resolve_with_text_capability():
    # The 13V Chief Architect chain needs every one of its candidates to be
    # a real text_task provider.
    import core.ai_provider as ai_provider

    for name in ai_router.ROLE_PROVIDERS["architecture"]:
        provider = ai_provider.get_provider(name)
        assert provider is not None, name
        assert provider.get("run_text_task") is not None, name


# --- 17R: AI routing resilience ----------------------------------------------
# 1. File-access-aware routing
# 2. Wall-clock degraded-state detection
# 3. Circuit-breaker with 60-second cooldown


# --- 17R.2: File-access-aware routing ----------------------------------------

def test_file_access_capability_is_registered_on_coding_agents():
    import core.ai_provider as ai_provider

    coding_agents = [n for n, p in ai_provider._PROVIDERS.items() if p.get("run_coding_task")]
    for name in coding_agents:
        provider = ai_provider.get_provider(name)
        assert "file_access" in provider.get("capabilities", []), name


def test_file_access_capability_not_on_text_only_providers():
    import core.ai_provider as ai_provider

    text_only = [
        n for n, p in ai_provider._PROVIDERS.items()
        if p.get("run_text_task") and not p.get("run_coding_task")
        and n not in ("local",)  # local is placeholder
    ]
    for name in text_only:
        provider = ai_provider.get_provider(name)
        assert "file_access" not in provider.get("capabilities", []), name


def test_delegate_with_requires_file_access_filters_out_text_only(monkeypatch):
    import core.ai_provider as ai_provider

    # Stub every text-task provider in "planning" except kai_brain
    # (which has file_access via its coding_agent capability).
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning:
        provider = ai_provider.get_provider(name)
        if name != "kai_brain":
            monkeypatch.setitem(provider, "available_fn", lambda: False)

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task",
                        lambda p, timeout=60, project_path=None: "kai_brain with file access")

    result = ai_router.delegate(
        "Read a file and respond", task_type="planning", requires_file_access=True,
    )

    assert result["provider"] == "kai_brain"


def test_delegate_with_requires_file_access_falls_through_text_providers(monkeypatch):
    import core.ai_provider as ai_provider

    # With requires_file_access, text-only candidates should be skipped and the
    # first file_access-capable candidate answers.
    for name in ai_router.ROLE_PROVIDERS["planning"]:
        provider = ai_provider.get_provider(name)
        if "file_access" in provider.get("capabilities", []):
            monkeypatch.setitem(provider, "available_fn", lambda: True)
            monkeypatch.setitem(provider, "run_text_task",
                                lambda p, timeout=60, project_path=None, n=name: f"from {n}")
        else:
            monkeypatch.setitem(provider, "available_fn", lambda: True)
            monkeypatch.setitem(provider, "run_text_task",
                                lambda p, timeout=60, project_path=None: pytest.fail("text-only provider must be skipped"))

    result = ai_router.delegate(
        "Design with file access", task_type="planning", requires_file_access=True,
    )

    # The first file_access-capable provider in "planning" is kai_brain.
    assert result["provider"] == "kai_brain"


def test_delegate_without_requires_file_access_does_not_filter(monkeypatch):
    import core.ai_provider as ai_provider

    # Without requires_file_access, text-only providers are used normally.
    # Disable all but the last planning provider.
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning[:-1]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    last = ai_provider.get_provider(planning[-1])
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(last, "run_text_task",
                        lambda p, timeout=60, project_path=None: "fallback text")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == planning[-1]


def test_dashboard_includes_file_access_flag():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["kai_coder"]["file_access"] is True
    assert dashboard["local"]["file_access"] is False
    assert dashboard["llama3"]["file_access"] is False


# --- 17R.3: Wall-clock latency degradation detection -------------------------

def test_provider_latency_record_and_baseline():
    import core.ai.provider_latency as pl

    pl.record_latency("test_prov", 100)
    pl.record_latency("test_prov", 120)
    pl.record_latency("test_prov", 110)

    snap = pl.get_latency_snapshot("test_prov")
    assert snap["count"] == 3
    assert 100 < snap["ema_ms"] < 120


def test_provider_latency_is_not_degraded_below_threshold():
    import core.ai.provider_latency as pl

    for d in (100, 100, 100, 100):
        pl.record_latency("stable_prov", d)

    assert pl.is_latency_degraded("stable_prov") is False


def test_provider_latency_is_degraded_when_spike_exceeds_factor_threshold():
    import core.ai.provider_latency as pl

    for d in (100, 100, 100):
        pl.record_latency("spiky_prov", d)

    # baseline ema ~100ms -- spike of 500ms is >3x, should degrade
    snap = pl.record_latency("spiky_prov", 500)
    assert snap["last_duration_ms"] == 500

    assert pl.is_latency_degraded("spiky_prov") is True


def test_provider_latency_not_degraded_with_insufficient_samples():
    import core.ai.provider_latency as pl

    pl.record_latency("new_prov", 5000)
    pl.record_latency("new_prov", 5000)

    assert pl.is_latency_degraded("new_prov") is False


def test_provider_latency_explicit_comparison():
    import core.ai.provider_latency as pl

    for d in (50, 50, 50):
        pl.record_latency("comp_prov", d)

    # Baseline ~50ms, 200ms is 4x -> degraded
    assert pl.is_latency_degraded("comp_prov", current_duration_ms=200) is True
    # Baseline ~50ms, 60ms is 1.2x -> not degraded
    assert pl.is_latency_degraded("comp_prov", current_duration_ms=60) is False


def test_provider_latency_unknown_provider_is_not_degraded():
    import core.ai.provider_latency as pl

    assert pl.is_latency_degraded("never_called") is False


def test_delegate_demotes_latency_degraded_provider(monkeypatch):
    # 17R: latency degradation demotes (tried last) rather than hard-excludes.
    # When the degraded provider is the only one available, it's still tried.
    import core.ai.provider_latency as pl
    import core.ai_provider as ai_provider

    # Disable all planning candidates ahead of the last one so it is reached.
    planning = ai_router.ROLE_PROVIDERS["planning"]
    last_name = planning[-1]
    for name in planning:
        if name != last_name:
            monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    fallback = ai_provider.get_provider(last_name)
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task",
                        lambda p, timeout=60, project_path=None: "fallback degraded but tried as last resort")

    # Mark fallback as latency-degraded with an extreme spike.
    pl.record_latency(last_name, 100)
    pl.record_latency(last_name, 100)
    pl.record_latency(last_name, 100)
    pl.record_latency(last_name, 5000)
    assert pl.is_latency_degraded(last_name) is True

    # With all other candidates disabled and fallback latency-degraded,
    # it's demoted but tried as last resort -- degradation is demotion, not exclusion.
    result = ai_router.delegate("Design an application architecture", return_attempts=True)

    assert result["provider"] == last_name

    degraded_notes = [a for a in result["attempts"] if a["error_type"] == "degraded_health"]
    assert len(degraded_notes) >= 1
    assert all(last_name in (a.get("provider") or "") for a in degraded_notes)


def test_delegate_records_latency_on_success(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_latency as pl

    # Disable all planning candidates except kai_brain.
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning:
        if name != "kai_brain":
            p = ai_provider.get_provider(name)
            if p:
                monkeypatch.setitem(p, "available_fn", lambda: False)

    primary = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", lambda p, timeout=60, project_path=None: "ok")

    ai_router.delegate("Design an application architecture")

    snap = pl.get_latency_snapshot("kai_brain")
    assert snap is not None
    assert snap["count"] == 1
    assert snap["last_duration_ms"] >= 0


def test_latency_degradation_syncs_to_provider_health():
    # 17R: when provider_latency.is_latency_degraded is True, record_latency
    # must also record the state in provider_health so the dashboard
    # surfaces it alongside circuit-breaker and quota state.
    import core.ai.provider_latency as pl
    import core.ai.provider_health as ph

    for d in (100, 100, 100, 100):
        pl.record_latency("lhd_test", d)
    pl.record_latency("lhd_test", 5000)

    assert pl.is_latency_degraded("lhd_test") is True

    snap = ph.get_quota_snapshot("lhd_test")
    assert snap is not None
    assert snap["status"] == "error"
    assert "latency degraded" in snap.get("detail", "")


def test_delegate_demotion_tries_healthy_before_degraded(monkeypatch):
    # 17R: when multiple candidates exist, healthy ones are tried before
    # latency-degraded ones (demotion, not exclusion).
    import core.ai.provider_latency as pl
    import core.ai_provider as ai_provider

    # Mark kai_brain (log_analysis fallback) as latency-degraded.
    for d in (100, 100, 100, 100):
        pl.record_latency("kai_brain", d)
    pl.record_latency("kai_brain", 5000)

    assert pl.is_latency_degraded("kai_brain") is True

    brain = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(brain, "available_fn", lambda: True)
    monkeypatch.setitem(brain, "run_text_task",
                        lambda p, timeout=60, project_path=None: "kai_brain degraded last resort")

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task",
                        lambda p, timeout=60, project_path=None: "local healthy primary")

    # local (healthy) should be tried before kai_brain (degraded)
    result = ai_router.delegate("Analyze Docker error log", task_type="log_analysis",
                                return_attempts=True)

    assert result["provider"] == "local"
    assert result["response"] == "local healthy primary"


# --- 17R.4: Circuit-breaker with 60-second cooldown -------------------------

def test_circuit_breaker_records_consecutive_failures():
    import core.ai.circuit_breaker as cb

    cb.record_failure("test_cb")
    cb.record_failure("test_cb")

    snap = cb.get_breaker_snapshot("test_cb")
    assert snap["consecutive_failures"] == 2
    assert snap["state"] == "closed"
    assert cb.is_open("test_cb") is False


def test_circuit_breaker_trips_after_threshold():
    import core.ai.circuit_breaker as cb

    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("tripping")

    snap = cb.get_breaker_snapshot("tripping")
    assert snap["consecutive_failures"] == cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD
    assert snap["state"] == "open"
    assert cb.is_open("tripping") is True


def test_circuit_breaker_clears_on_success():
    import core.ai.circuit_breaker as cb

    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("clearing")

    assert cb.is_open("clearing") is True

    cb.record_success("clearing")
    assert cb.is_open("clearing") is False
    assert cb.get_breaker_snapshot("clearing") is None


def test_circuit_breaker_transitions_to_half_open_after_cooldown(monkeypatch):
    import core.ai.circuit_breaker as cb
    from datetime import datetime, timedelta

    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("cooldown_test")

    assert cb.is_open("cooldown_test") is True

    # Simulate that the cooldown has elapsed by backing up the tripped_at
    # timestamp in the state.
    state = cb._load_state()
    past = (datetime.now() - timedelta(seconds=cb.CIRCUIT_BREAKER_COOLDOWN_SECONDS + 1))
    state["cooldown_test"]["tripped_at"] = past.isoformat()
    cb._save_state(state)

    # Now is_open should return False (circuit is half-open)
    assert cb.is_open("cooldown_test") is False
    assert cb.get_breaker_snapshot("cooldown_test")["state"] == "half_open"


def test_circuit_breaker_unknown_provider_is_not_open():
    import core.ai.circuit_breaker as cb

    assert cb.is_open("unknown_provider") is False
    assert cb.get_breaker_snapshot("unknown_provider") is None


def test_circuit_breaker_clear_breaker():
    import core.ai.circuit_breaker as cb

    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("clear_me")

    assert cb.is_open("clear_me") is True
    cb.clear_breaker("clear_me")
    assert cb.is_open("clear_me") is False


def test_delegate_skips_circuit_open_provider(monkeypatch):
    import core.ai.circuit_breaker as cb
    import core.ai_provider as ai_provider

    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("local")

    assert cb.is_open("local") is True

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task",
                        lambda p, timeout=60, project_path=None: pytest.fail("local is circuit-open, must be skipped"))

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task",
                        lambda p, timeout=60, project_path=None: "kai_brain to the rescue")

    result = ai_router.delegate("Analyze Docker error log", task_type="log_analysis")

    assert result["provider"] == "kai_brain"


def test_delegate_records_circuit_breaker_on_failure(monkeypatch):
    import core.ai.circuit_breaker as cb
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("connection refused")

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task", boom)

    fallback = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task",
                        lambda p, timeout=60, project_path=None: "kai_brain saved")

    ai_router.delegate("Analyze Docker error log", task_type="log_analysis")

    snap = cb.get_breaker_snapshot("local")
    assert snap["consecutive_failures"] == 1
    assert snap["state"] == "closed"


def test_delegate_clears_circuit_breaker_on_success(monkeypatch):
    import core.ai.circuit_breaker as cb
    import core.ai_provider as ai_provider

    # Pre-set the breaker to open
    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("local")

    assert cb.is_open("local") is True

    # Manually transition to half-open and have the attempt succeed
    cb._save_state({})

    local = ai_provider.get_provider("local")
    monkeypatch.setitem(local, "available_fn", lambda: True)
    monkeypatch.setitem(local, "run_text_task",
                        lambda p, timeout=60, project_path=None: "local is back")

    # local is no longer open (cleared above) -- it should succeed and the
    # breaker should stay cleared.
    result = ai_router.delegate("Analyze Docker error log", task_type="log_analysis")

    assert result["provider"] == "local"
    assert cb.is_open("local") is False


def test_dashboard_includes_circuit_breaker_and_latency():
    import core.ai.circuit_breaker as cb
    import core.ai.provider_latency as pl

    cb.record_failure("local")
    pl.record_latency("local", 200)

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["local"]["circuit_breaker"] is not None
    assert dashboard["local"]["circuit_breaker"]["consecutive_failures"] == 1
    assert dashboard["local"]["latency"] is not None
    assert dashboard["local"]["latency"]["last_duration_ms"] == 200


# ── 18A-ai Phase 2: provider override ────────────────────────────────────


def test_delegate_provider_override_routes_to_specified_provider(monkeypatch):
    """When provider='local', delegate() tries ONLY 'local' and returns its result."""
    # Disable all automated classification and rotation — we test the
    # override path exclusively.
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")
    monkeypatch.setattr(ai_router, "_candidates_for", lambda _: [])

    # Mock the local provider with an inline dict (no dependency on real
    # registration) to return a known response.
    import core.ai_provider as ai_provider
    called_with = []

    mock_provider = {
        "run_text_task": lambda p, **kw: called_with.append(p) or "response from local",
        "available_fn": lambda: True,
        "enabled": True,
        "capabilities": ["text_task"],
    }
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "local" else None)

    # Disable health/quota/circuit checks that could block.
    monkeypatch.setattr(ai_router.provider_health, "get_quota_snapshot", lambda _: None)
    monkeypatch.setattr(ai_router.circuit_breaker, "is_open", lambda _: False)

    result = ai_router.delegate("test prompt", provider="local")

    assert result["provider"] == "local"
    assert result["response"] == "response from local"
    assert called_with == ["test prompt"]


def test_delegate_provider_override_raises_when_provider_not_registered(monkeypatch):
    """When provider='nonexistent', delegate() raises AllProvidersFailed immediately."""
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")
    # Disable candidate rotation so the test fails fast regardless of
    # where the implementation places the provider check.
    monkeypatch.setattr(ai_router, "_candidates_for", lambda _: [])

    with pytest.raises(AllProvidersFailed) as exc_info:
        ai_router.delegate("test", provider="nonexistent_provider_xyz")

    assert "nonexistent_provider_xyz" in str(exc_info.value)
    # attempts should contain the failure record
    assert exc_info.value.attempts
    assert exc_info.value.attempts[0]["provider"] == "nonexistent_provider_xyz"


def test_delegate_provider_override_raises_when_provider_unavailable(monkeypatch):
    """When the specified provider's available_fn returns False, delegate() raises."""
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")

    import core.ai_provider as ai_provider
    mock_provider = {
        "run_text_task": lambda p, **kw: "should not be called",
        "available_fn": lambda: False,
        "enabled": True,
        "capabilities": ["text_task"],
    }
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "fake_prov" else None)

    with pytest.raises(AllProvidersFailed) as exc_info:
        ai_router.delegate("test", provider="fake_prov")

    assert "fake_prov" in str(exc_info.value)
    assert exc_info.value.attempts[0]["error_type"] == "unavailable"


def test_delegate_without_provider_override_unchanged(monkeypatch):
    """When provider=None (default), behavior is identical to before."""
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")

    import core.ai_provider as ai_provider
    mock_provider = {
        "run_text_task": lambda p, **kw: "auto-routed result",
        "available_fn": lambda: True,
        "enabled": True,
        "capabilities": ["text_task"],
    }
    # _candidates_for returns a list; delegate() rotates and iterates.
    monkeypatch.setattr(ai_router, "_candidates_for", lambda _: ["mock"])
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "mock" else None)
    monkeypatch.setattr(ai_router.provider_health, "get_quota_snapshot", lambda _: None)
    monkeypatch.setattr(ai_router.circuit_breaker, "is_open", lambda _: False)

    result = ai_router.delegate("test")  # no provider= kwarg

    assert result["provider"] == "mock"
    assert result["response"] == "auto-routed result"


# ── 2026-08-26: token usage threading into usage history ─────────────────

def test_delegate_records_provider_usage_in_history_entry(monkeypatch):
    # The success-path record_usage call must pick up the usage block
    # captured by llm_clients during run_fn, so cost_tracker can estimate
    # real spend instead of recording $0 for every entry.
    import core.ai_provider as ai_provider
    import core.llm_clients as llm_clients

    primary = ai_provider.get_provider("kai_brain")
    monkeypatch.setitem(primary, "available_fn", lambda: True)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ["kai_brain"],
    })

    def fake_run(p, timeout=60, project_path=None):
        llm_clients._last_call_usage.value = {"prompt_tokens": 500, "completion_tokens": 120}
        return "answered"

    monkeypatch.setitem(primary, "run_text_task", fake_run)

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "kai_brain"
    history = ai_router.get_usage_history()
    entry = history[-1]
    assert entry["success"] is True
    assert entry["usage"] == {"prompt_tokens": 500, "completion_tokens": 120}
    # Threaded capture must be consumed, not left dangling for the next call.
    assert llm_clients.pop_last_usage() is None


def test_delegate_failure_with_captured_usage_still_records_it(monkeypatch):
    import core.ai_provider as ai_provider
    import core.llm_clients as llm_clients

    def boom(p, timeout=60, project_path=None):
        llm_clients._last_call_usage.value = {"prompt_tokens": 300, "completion_tokens": 0}
        raise RuntimeError("boom")

    monkeypatch.setattr(ai_router, "_candidates_for", lambda _: ["mock"])
    mock_provider = {
        "name": "mock",
        "enabled": True,
        "available_fn": lambda: True,
        "capabilities": ["text_task"],
        "run_text_task": boom,
    }
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "mock" else None)
    monkeypatch.setattr(ai_router.provider_health, "get_quota_snapshot", lambda _: None)
    monkeypatch.setattr(ai_router.circuit_breaker, "is_open", lambda _: False)

    with pytest.raises(ai_router.AllProvidersFailed):
        ai_router.delegate("test")

    history = ai_router.get_usage_history()
    failed_entry = [e for e in history if not e["success"]][-1]
    assert failed_entry["usage"] == {"prompt_tokens": 300, "completion_tokens": 0}
