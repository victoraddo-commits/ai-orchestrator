import pytest

import core.ai.ai_router as ai_router
from core.ai.ai_router import AllProvidersFailed


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
    ("Design an application architecture", "gemini"),
    ("Build authentication system", "claude"),
    ("Analyze Docker error log", "groq"),
])
def test_delegate_routes_to_expected_provider(monkeypatch, description, expected_provider):
    import core.ai_provider as ai_provider

    for name in ("claude", "gemini", "groq", "openai"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        if provider.get("run_text_task"):
            monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"response from {n}")

    result = ai_router.delegate(description)

    assert result["provider"] == expected_provider


def test_delegate_documentation_task_accepts_gemini_or_groq(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("claude", "gemini", "groq"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        if provider.get("run_text_task"):
            monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"response from {n}")

    result = ai_router.delegate("Generate README documentation")

    assert result["provider"] in ("gemini", "groq")


def test_delegate_falls_back_when_first_choice_unavailable(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "openrouter", "deepseek", "minimax"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_falls_back_when_first_choice_call_raises(monkeypatch):
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("gemini quota exceeded")

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", boom)

    for name in ("openrouter", "deepseek", "minimax"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_raises_when_every_candidate_fails(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "openrouter", "deepseek", "minimax", "claude"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: False)

    with pytest.raises(AllProvidersFailed):
        ai_router.delegate("Design an application architecture")


def test_delegate_skips_a_candidate_known_to_be_quota_exceeded_without_calling_it(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    provider_health.capture_quota_exceeded("gemini", detail="daily quota exhausted")

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(
        gemini, "run_text_task",
        lambda p, timeout=60, project_path=None: pytest.fail("gemini should have been skipped, not called"),
    )

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    for name in ("openrouter", "deepseek", "minimax"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_still_tries_a_candidate_with_only_a_recorded_error_not_quota_exceeded(monkeypatch):
    # provider_health deliberately never equates a raw "error" status with
    # confirmed quota exhaustion (it can't tell a transient network blip
    # from a real limit) -- only a verified quota_exceeded status should
    # cause delegate() to skip a call outright.
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    provider_health.capture_provider_error("gemini", detail="ConnectionError")

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", lambda p, timeout=60, project_path=None: "gemini recovered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "gemini"


def test_delegate_records_usage_on_success(monkeypatch):
    import core.ai_provider as ai_provider

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    history = ai_router.get_usage_history()
    assert len(history) == 1
    assert history[0]["provider"] == "gemini"
    assert history[0]["success"] is True
    assert history[0]["task_type"] == "planning"


def test_delegate_records_usage_on_failure_too(monkeypatch):
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("boom")

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", boom)

    for name in ("openrouter", "deepseek", "minimax"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "ok")

    ai_router.delegate("Design an application architecture")

    history = ai_router.get_usage_history()
    assert len(history) == 2
    assert history[0]["provider"] == "gemini"
    assert history[0]["success"] is False
    assert history[1]["provider"] == "claude"
    assert history[1]["success"] is True


def test_delegate_with_coding_agent_capability_calls_run_coding_task_not_run_text_task(monkeypatch):
    import core.ai_provider as ai_provider

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_text_task",
        lambda *a, **k: pytest.fail("run_text_task should not be called for capability=coding_agent"),
    )

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["project_path"] = project_path
        captured["instruction"] = instruction
        return {"success": True, "response_text": "done", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setitem(claude, "run_coding_task", fake_run_coding_task)

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    assert result["provider"] == "claude"
    assert captured["project_path"] == "/proj"
    assert captured["instruction"] == "Implement the widget"
    assert result["response"]["success"] is True


def test_delegate_with_coding_agent_capability_falls_back_to_opencode_when_claude_fails(monkeypatch):
    import core.ai_provider as ai_provider

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": False, "response_text": "", "files_changed": [], "commits": [], "tool_errors": [{"tool": None, "content": "boom"}]},
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": ["a.py"], "commits": [], "tool_errors": []},
    )

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    # Claude's call "succeeded" at the transport level (no exception) but the
    # task itself failed -- delegate()'s coding_agent path must fall through
    # to the next candidate on a result-level failure, not just an exception,
    # since a failed generation is exactly the case that must not stall Kai.
    assert result["provider"] == "opencode"
    assert result["response"]["files_changed"] == ["a.py"]


def test_delegate_records_a_confirmed_usage_limit_message_as_quota_exceeded(monkeypatch):
    # Confirmed live: Claude Code returned "You've hit your weekly limit --
    # resets Jul 29, 1pm" mid-generation. Without this, delegate() would
    # keep retrying Claude every cycle for the next day despite the failure
    # being unambiguous and durable, not transient.
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "Claude Code returned an error result: You've hit your weekly limit · resets Jul 29, 1pm"}],
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("claude")
    assert snapshot["status"] == "quota_exceeded"
    assert "weekly limit" in snapshot["detail"].lower()


def test_delegate_records_a_generic_coding_failure_as_error_not_quota_exceeded(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": "Bash", "content": "tests failed"}],
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("claude")
    assert snapshot["status"] == "error"


def test_delegate_records_opencode_credit_exhaustion_as_quota_exceeded_and_notifies(monkeypatch):
    # User directive 2026-07-30: every opencode_* provider failed generically
    # ("generation did not succeed") during what turned out to be a real
    # OpenCode Zen credit exhaustion -- add best-effort marker phrases and
    # notify the user the first time it's detected.
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health
    import core.telegram_bridge as telegram_bridge

    sent = []
    monkeypatch.setattr(telegram_bridge, "send_message", lambda text: sent.append(text))

    opencode_claude = ai_provider.get_provider("opencode_claude")
    monkeypatch.setitem(opencode_claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode_claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "Error: insufficient credit balance"}],
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["opencode_claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("opencode_claude")
    assert snapshot["status"] == "quota_exceeded"
    assert "insufficient credit" in snapshot["detail"].lower()
    assert len(sent) == 1
    assert "opencode_claude" in sent[0]


def test_delegate_does_not_renotify_once_already_quota_exceeded(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health
    import core.telegram_bridge as telegram_bridge

    sent = []
    monkeypatch.setattr(telegram_bridge, "send_message", lambda text: sent.append(text))
    provider_health.capture_quota_exceeded("opencode_claude", detail="already known: insufficient credit balance")

    opencode_claude = ai_provider.get_provider("opencode_claude")
    monkeypatch.setitem(opencode_claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode_claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "Error: insufficient credit balance, still exhausted"}],
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["opencode_claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    assert sent == []


def test_delegate_does_not_notify_for_non_opencode_quota_exceeded(monkeypatch):
    # The notification is specifically about the shared OpenCode Zen
    # account -- Claude's own weekly-limit quota_exceeded must not trigger
    # the opencode-specific alert.
    import core.ai_provider as ai_provider
    import core.telegram_bridge as telegram_bridge

    sent = []
    monkeypatch.setattr(telegram_bridge, "send_message", lambda text: sent.append(text))

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "You've hit your weekly limit"}],
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    assert sent == []


def test_opencode_quota_notify_failure_does_not_break_delegate(monkeypatch):
    # A Telegram outage must never surface as a build/generation failure.
    import core.ai_provider as ai_provider
    import core.telegram_bridge as telegram_bridge

    def _boom(text):
        raise RuntimeError("Telegram sendMessage failed")

    monkeypatch.setattr(telegram_bridge, "send_message", _boom)

    opencode_claude = ai_provider.get_provider("opencode_claude")
    monkeypatch.setitem(opencode_claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode_claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": None, "content": "Error: insufficient credit balance"}],
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["opencode_claude", "opencode"]})

    result = ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    assert result["response"]["success"] is True


def test_delegate_accepts_explicit_task_type_override(monkeypatch):
    import core.ai_provider as ai_provider

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task", lambda p, timeout=60, project_path=None: "forced")

    result = ai_router.delegate("some ambiguous text", task_type="log_analysis")

    assert result["provider"] == "groq"
    assert result["task_type"] == "log_analysis"


def test_delegate_review_task_type_routes_to_openai(monkeypatch):
    import core.ai_provider as ai_provider

    openai = ai_provider.get_provider("openai")
    monkeypatch.setitem(openai, "available_fn", lambda: True)
    monkeypatch.setitem(openai, "run_text_task", lambda p, timeout=60, project_path=None: "reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "openai"


def test_delegate_review_task_type_falls_back_to_gemini_then_claude(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("openai"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("deepseek"), "available_fn", lambda: False)

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", lambda p, timeout=60, project_path=None: "gemini reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "gemini"


def test_get_provider_dashboard_summarizes_last_request_per_provider(monkeypatch):
    import core.ai_provider as ai_provider

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    dashboard = ai_router.get_provider_dashboard()

    assert "gemini" in dashboard
    assert dashboard["gemini"]["status"] == "connected"
    assert dashboard["gemini"]["last_task_type"] == "planning"
    assert dashboard["gemini"]["last_success"] is True
    assert dashboard["gemini"]["last_response_time_ms"] is not None


def test_get_provider_dashboard_shows_not_configured_for_unavailable_providers():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["local"]["status"] == "not_configured"


def test_get_provider_dashboard_includes_quota_percent_when_known():
    import core.ai.provider_health as provider_health

    provider_health.record_quota_snapshot("groq", status="ok", percent_remaining=87.5)

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["groq"]["percent_remaining"] == 87.5


def test_get_provider_dashboard_shows_none_percent_when_quota_never_checked():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["gemini"]["percent_remaining"] is None


def test_get_provider_dashboard_surfaces_a_recorded_claude_error(monkeypatch):
    import core.ai.provider_health as provider_health

    provider_health.capture_provider_error("claude", detail="Claude usage limit reached. Resets at 3pm.")

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["claude"]["quota_detail"] == "Claude usage limit reached. Resets at 3pm."
    assert dashboard["claude"]["percent_remaining"] is None


def test_delegate_rotates_starting_candidate_across_successive_calls(monkeypatch):
    # A strict fallback-only order starves whichever candidate never fails
    # first (e.g. gemini for planning) -- rotation gives every role
    # candidate a real turn as the first attempt so idle credits (openrouter,
    # minimax, ...) actually get used instead of sitting untouched.
    import core.ai_provider as ai_provider

    review_candidates = ["openai", "gemini", "deepseek", "claude"]
    for name in review_candidates:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    seen = [ai_router.delegate("Critique this design", task_type="review")["provider"] for _ in range(5)]

    assert seen == review_candidates + ["openai"]


def test_delegate_rotation_is_tracked_independently_per_task_type(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("openai", "gemini", "claude", "groq", "openrouter"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    first = ai_router.delegate("Critique this design", task_type="review")["provider"]
    log_result = ai_router.delegate("Check the logs", task_type="log_analysis")["provider"]
    second = ai_router.delegate("Critique this design", task_type="review")["provider"]

    assert [first, second] == ["openai", "gemini"]
    assert log_result == "groq"


def test_delegate_rotation_still_falls_through_to_next_candidate_on_failure(monkeypatch):
    import core.ai_provider as ai_provider

    openai = ai_provider.get_provider("openai")
    monkeypatch.setitem(openai, "available_fn", lambda: True)
    monkeypatch.setitem(openai, "run_text_task", lambda p, timeout=60, project_path=None: "from openai")

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("gemini down")

    monkeypatch.setitem(gemini, "run_text_task", boom)

    monkeypatch.setitem(ai_provider.get_provider("deepseek"), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "from claude")

    # First call rotates to "openai" (index 0) and succeeds there.
    first = ai_router.delegate("Critique this design", task_type="review")["provider"]
    # Second call rotates its starting point to "gemini", which fails --
    # fallback must still walk forward past unavailable deepseek to "claude", not raise.
    second = ai_router.delegate("Critique this design", task_type="review")["provider"]

    assert [first, second] == ["openai", "claude"]


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


def test_get_provider_dashboard_claude_uses_self_tracked_usage_not_quota_state(monkeypatch):
    monkeypatch.setattr(
        ai_router, "get_usage_history",
        lambda: [{"provider": "claude", "success": True, "timestamp": "2026-07-28T00:00:00",
                   "task_type": "coding", "duration_ms": 100}],
    )

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["claude"]["percent_remaining"] is None
    assert "self-tracked" in dashboard["claude"]["quota_detail"].lower()


# --- 13W: real per-call cost capture + workforce analytics aggregation ------

def test_record_usage_stores_a_provider_reported_cost():
    entry = ai_router.record_usage("opencode", "coding", "build x", success=True, duration_ms=1200, cost=0.0139422)

    assert entry["cost"] == 0.0139422
    assert ai_router.get_usage_history()[-1]["cost"] == 0.0139422


def test_record_usage_defaults_cost_to_null_not_an_estimate():
    entry = ai_router.record_usage("gemini", "planning", "plan x", success=True, duration_ms=800)

    assert entry["cost"] is None


def test_delegate_coding_agent_records_the_cost_reported_by_the_provider(monkeypatch):
    import core.ai_provider as ai_provider

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
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

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
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

    gemini = ai_provider.get_provider("gemini")
    monkeypatch.setitem(gemini, "available_fn", lambda: True)
    monkeypatch.setitem(gemini, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    assert ai_router.get_usage_history()[-1]["cost"] is None


def test_delegate_records_cost_even_for_a_result_level_failure(monkeypatch):
    # A failed generation still incurred the cost the provider billed for it.
    import core.ai_provider as ai_provider

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {
            "success": False, "response_text": "", "files_changed": [], "commits": [],
            "tool_errors": [{"tool": "Bash", "content": "tests failed"}], "cost": 0.002,
        },
    )

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    history = ai_router.get_usage_history()
    failed = next(e for e in history if e["provider"] == "claude")
    assert failed["success"] is False
    assert failed["cost"] == 0.002


def test_get_provider_dashboard_aggregates_cost_totals_and_average_duration(monkeypatch):
    monkeypatch.setattr(
        ai_router, "get_usage_history",
        lambda: [
            {"provider": "opencode", "success": True, "timestamp": "2026-07-30T00:00:00",
             "task_type": "coding", "duration_ms": 100, "cost": 0.01},
            {"provider": "opencode", "success": False, "timestamp": "2026-07-30T00:01:00",
             "task_type": "coding", "duration_ms": 300, "cost": 0.02},
            # a pre-13W entry with no cost key at all must not break the sum
            {"provider": "opencode", "success": True, "timestamp": "2026-07-30T00:02:00",
             "task_type": "coding", "duration_ms": 200},
        ],
    )

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["opencode"]["total_cost"] == pytest.approx(0.03)
    assert dashboard["opencode"]["cost_reported_calls"] == 2
    assert dashboard["opencode"]["average_duration_ms"] == pytest.approx(200.0)
    assert dashboard["opencode"]["total_attempts"] == 3
    assert dashboard["opencode"]["total_successes"] == 2


def test_get_provider_dashboard_total_cost_is_null_when_no_call_ever_reported_one(monkeypatch):
    # "no cost data" must stay distinguishable from "cost zero" -- never
    # display a fabricated 0.0 for a provider that doesn't report cost.
    monkeypatch.setattr(
        ai_router, "get_usage_history",
        lambda: [{"provider": "gemini", "success": True, "timestamp": "2026-07-30T00:00:00",
                  "task_type": "planning", "duration_ms": 500, "cost": None}],
    )

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["gemini"]["total_cost"] is None
    assert dashboard["gemini"]["cost_reported_calls"] == 0
    assert dashboard["gemini"]["average_duration_ms"] == 500


def test_get_provider_dashboard_shows_null_aggregates_for_a_provider_with_no_history():
    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["local"]["total_cost"] is None
    assert dashboard["local"]["average_duration_ms"] is None


def test_get_provider_dashboard_surfaces_each_providers_cost_tier():
    import core.ai_provider as ai_provider

    dashboard = ai_router.get_provider_dashboard()

    for name, entry in dashboard.items():
        assert entry["cost_tier"] in ai_provider.COST_TIERS, name

    assert dashboard["gemini"]["cost_tier"] == "free"
    assert dashboard["claude"]["cost_tier"] == "paid"


# --- 13T: evidence-based minimax routing ------------------------------------

TEXT_TASK_ROLES = ("planning", "log_analysis", "documentation", "review")


@pytest.mark.parametrize("role", TEXT_TASK_ROLES)
def test_minimax_is_not_in_any_text_task_role(role):
    # 13T usage-history review: 4 recorded planning attempts, 3 flagged
    # "success", but every content-bearing one was hallucinated
    # <minimax:tool_call> markup (builds ca7ff314/13P, 56e6c3d7/13R,
    # e75e4848/13Q) and the fourth was a ConnectionError -- 0/4 usable.
    # log_analysis/documentation have no recorded attempts at all, but share
    # the identical tools-less core.llm_clients.call_minimax code path.
    assert "minimax" not in ai_router.ROLE_PROVIDERS[role]


def test_minimax_coding_agent_route_is_in_the_coding_rotation():
    # The other half of the same review: minimax-m2.7 through opencode CLI's
    # real tool-use loop is 3/3 recorded, with zero hallucinated-tool-call,
    # timeout or tool-error events -- the 2026-07-28 blanket pause was
    # over-broad for this path.
    assert "opencode_minimax" in ai_router.ROLE_PROVIDERS["coding"]


def test_minimax_coding_agent_route_is_not_ahead_of_the_claude_family():
    # "observe", not "trusted": 3 attempts is below MIN_SAMPLE_SIZE, so it
    # earns a place in the rotation, not the front of it.
    coding = ai_router.ROLE_PROVIDERS["coding"]

    assert coding.index("opencode_minimax") > coding.index("claude")
    assert coding.index("opencode_minimax") > coding.index("opencode_claude")


def test_coding_role_still_ends_on_a_claude_family_universal_fallback():
    assert "claude" in ai_router.ROLE_PROVIDERS["coding"]


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


def test_delegate_falls_through_to_opencode_minimax_when_the_others_fail(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("claude", "opencode_claude", "opencode_minimax"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)

    def fail(project_path, instruction, timeout=60):
        raise RuntimeError("nope")

    monkeypatch.setitem(ai_provider.get_provider("claude"), "run_coding_task", fail)
    monkeypatch.setitem(ai_provider.get_provider("opencode_claude"), "run_coding_task", fail)
    monkeypatch.setitem(
        ai_provider.get_provider("opencode_minimax"),
        "run_coding_task",
        lambda project_path, instruction, timeout=60: {"success": True, "response_text": "done"},
    )
    monkeypatch.setattr(
        ai_router,
        "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode_claude", "opencode_minimax"]},
    )

    result = ai_router.delegate("Build a widget", capability="coding_agent", project_path="/tmp/x")

    assert result["provider"] == "opencode_minimax"


# --- 13U: deepseek text-task + opencode_deepseek coding-agent routing ------

def test_delegate_planning_task_includes_deepseek_as_a_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "openrouter"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    deepseek = ai_provider.get_provider("deepseek")
    monkeypatch.setitem(deepseek, "available_fn", lambda: True)
    monkeypatch.setitem(deepseek, "run_text_task", lambda p, timeout=60, project_path=None: "deepseek planned")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "deepseek"


def test_delegate_documentation_task_includes_deepseek_as_a_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "groq", "openrouter"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    deepseek = ai_provider.get_provider("deepseek")
    monkeypatch.setitem(deepseek, "available_fn", lambda: True)
    monkeypatch.setitem(deepseek, "run_text_task", lambda p, timeout=60, project_path=None: "deepseek documented")

    result = ai_router.delegate("Generate README documentation")

    assert result["provider"] == "deepseek"


def test_delegate_review_task_includes_deepseek_as_a_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("openai"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("gemini"), "available_fn", lambda: False)

    deepseek = ai_provider.get_provider("deepseek")
    monkeypatch.setitem(deepseek, "available_fn", lambda: True)
    monkeypatch.setitem(deepseek, "run_text_task", lambda p, timeout=60, project_path=None: "deepseek reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "deepseek"


def test_delegate_coding_task_includes_opencode_deepseek_as_a_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("claude", "opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setitem(ai_provider.get_provider("opencode"), "available_fn", lambda: False)

    opencode_deepseek = ai_provider.get_provider("opencode_deepseek")
    monkeypatch.setitem(opencode_deepseek, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode_deepseek, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    assert result["provider"] == "opencode_deepseek"


def test_delegate_coding_task_opencode_deepseek_uses_correct_model(monkeypatch):
    import core.ai_provider as ai_provider
    import core.opencode_bridge as opencode_bridge

    for name in ("claude", "opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus", "opencode"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    opencode_deepseek = ai_provider.get_provider("opencode_deepseek")
    monkeypatch.setitem(opencode_deepseek, "available_fn", lambda: True)

    captured = {}

    def fake_run_coding_task(project_path, instruction, **kwargs):
        captured["model"] = kwargs.get("model")
        return {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []}

    monkeypatch.setattr(opencode_bridge, "run_coding_task", fake_run_coding_task)

    ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    assert captured["model"] == "openrouter/deepseek/deepseek-v4-pro"
