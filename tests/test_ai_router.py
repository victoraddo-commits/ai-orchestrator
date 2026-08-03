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
    # gemini re-enabled 2026-08-02 (credit reloaded) -- back to its original
    # evidence-based "planning" primary now that its quota problem is gone.
    ("Design an application architecture", "gemini"),
    ("Build authentication system", "claude"),
    ("Analyze Docker error log", "groq"),
])
def test_delegate_routes_to_expected_provider(monkeypatch, description, expected_provider):
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 (Fable 5 Q&A,
    # see ai_provider._opencode_claude_run_text_task) and now leads both
    # "coding" and "planning" -- disabled here so this stays a fast,
    # network-free unit test rather than a real opencode/Zen call.
    # deepseek_native_pro joined "planning" the same day (real network call
    # via api.deepseek.com if left available) -- disabled for the same reason.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude", "deepseek_native_pro"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    for name in ("claude", "gemini", "groq", "openai"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        if provider.get("run_text_task"):
            monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"response from {n}")

    result = ai_router.delegate(description)

    assert result["provider"] == expected_provider


def test_delegate_documentation_task_accepts_gemini_or_groq(monkeypatch):
    import core.ai_provider as ai_provider

    # deepseek_native_flash now sits ahead of groq/gemini in "documentation"
    # (2026-08-02 gemini delegation) -- disable it so this test still
    # exercises the groq/gemini choice it's named for.
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)

    for name in ("claude", "gemini", "groq"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        if provider.get("run_text_task"):
            monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"response from {n}")

    result = ai_router.delegate("Generate README documentation")

    assert result["provider"] in ("gemini", "groq")


def test_delegate_falls_back_when_first_choice_unavailable(monkeypatch):
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 and now sits
    # in "planning" too -- disabled so this test doesn't make a real
    # opencode/Zen call.
    for name in ("deepseek_native_flash", "gemini", "openrouter", "deepseek", "minimax", "opencode_claude", "deepseek_native_pro", "geminix"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_falls_back_when_first_choice_call_raises(monkeypatch):
    # "planning"'s actual first choice is deepseek_native_flash as of the
    # 2026-08-02 gemini delegation -- that's the one exercised raising here,
    # not gemini (now last, never reached once claude below succeeds).
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("deepseek_native_flash quota exceeded")

    primary = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", boom)

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call.
    for name in ("gemini", "openrouter", "deepseek", "minimax", "opencode_claude", "deepseek_native_pro", "geminix"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_raises_when_every_candidate_fails(monkeypatch):
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 -- included
    # here so every "planning" candidate really is unavailable.
    for name in ("deepseek_native_flash", "gemini", "openrouter", "deepseek", "minimax", "claude", "opencode_claude", "deepseek_native_pro", "geminix"):
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

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "minimax", "opencode_claude", "deepseek_native_pro", "geminix"):
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

    # gemini removed from "planning" entirely 2026-08-02 (disabled, not just
    # deprioritized) -- claude demonstrates the same "error" != "quota_exceeded"
    # distinction now, as the last remaining candidate in that role.
    provider_health.capture_provider_error("claude", detail="ConnectionError")

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude", "deepseek_native_pro", "gemini", "geminix"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude recovered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_records_usage_on_success(monkeypatch):
    # gemini removed from "planning" entirely 2026-08-02 -- claude (now
    # last) demonstrates usage-recording instead. opencode_claude gained a
    # real text_task route the same day -- disabled so this test doesn't
    # make a real opencode/Zen call.
    import core.ai_provider as ai_provider

    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude", "deepseek_native_pro", "gemini", "geminix", "qwen3_coder_text"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    history = ai_router.get_usage_history()
    assert len(history) == 1
    assert history[0]["provider"] == "claude"
    assert history[0]["success"] is True
    assert history[0]["task_type"] == "planning"


def test_delegate_records_usage_on_failure_too(monkeypatch):
    # "planning"'s actual first choice is deepseek_native_flash as of the
    # 2026-08-02 gemini delegation -- that's the one exercised failing here.
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("boom")

    primary = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", boom)

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call (it would otherwise
    # be tried between deepseek_native_flash's failure and claude's success,
    # breaking the exact 2-entry history this test asserts below).
    for name in ("openrouter", "deepseek", "minimax", "gemini", "geminix", "opencode_claude", "deepseek_native_pro", "qwen3_coder_text"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "ok")

    ai_router.delegate("Design an application architecture")

    history = ai_router.get_usage_history()
    assert len(history) == 2
    assert history[0]["provider"] == "deepseek_native_flash"
    assert history[0]["success"] is False
    assert history[1]["provider"] == "claude"
    assert history[1]["success"] is True


def test_delegate_with_coding_agent_capability_calls_run_coding_task_not_run_text_task(monkeypatch):
    import core.ai_provider as ai_provider

    # 13M: every candidate ahead of claude in the coding order must be
    # stubbed unavailable for claude to be the one that answers. qwen3_coding
    # joined 2026-08-02 (real, env-var-available in this process since
    # core.api's load_dotenv() leaks VLLM_QWEN3_CODER_* into the whole
    # pytest session) -- included so it doesn't make a real opencode call.
    for name in ("openrouter_claude_opus", "opencode_claude", "openrouter_claude_sonnet",
                 "opencode_claude_sonnet", "opencode_claude_opus", "qwen3_coding"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

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


def test_delegate_review_task_type_falls_back_to_claude_as_last_resort(monkeypatch):
    # gemini removed from "review" entirely 2026-08-02 (was the
    # second-to-last fallback here before being disabled) -- claude is now
    # review's genuine last resort.
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("openai"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("deepseek"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)
    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined "review" --
    # disabled so this test still exercises claude as the genuine last resort.
    monkeypatch.setitem(ai_provider.get_provider("gemini"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("geminix"), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "claude"


def test_get_provider_dashboard_summarizes_last_request_per_provider(monkeypatch):
    # gemini removed from "planning" entirely 2026-08-02 -- claude (now
    # last) demonstrates the dashboard summary instead. gemini itself stays
    # a registered provider (still listed in the dashboard, see the
    # dedicated test below), just never routed to right now.
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude", "deepseek_native_pro", "gemini", "geminix"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture")

    dashboard = ai_router.get_provider_dashboard()

    assert "claude" in dashboard
    assert dashboard["claude"]["status"] == "connected"
    assert dashboard["claude"]["last_task_type"] == "planning"
    assert dashboard["claude"]["last_success"] is True
    assert dashboard["claude"]["last_response_time_ms"] is not None


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
    # first (e.g. openai for review) -- rotation gives every role candidate
    # a real turn as the first attempt so idle credits (openrouter, minimax,
    # ...) actually get used instead of sitting untouched. ("review" is used
    # here rather than "planning" because planning is now FIXED_ORDER --
    # gemini's lead there is evidence-backed, not just a default.)
    import core.ai_provider as ai_provider

    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined "review".
    # qwen3_coder_text added 2026-08-03 (17Z) as fallback capacity.
    review_candidates = ["openai", "deepseek_native_flash", "deepseek", "gemini", "geminix", "qwen3_coder_text", "claude"]
    for name in review_candidates:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    # Call n+1 times (rotate through all candidates once, wrapping back to
    # the first entry "openai" on call 8).
    seen = [ai_router.delegate("Critique this design", task_type="review")["provider"] for _ in range(8)]

    assert seen == review_candidates + ["openai"]


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

    # "review"'s rotation index 1 is deepseek_native_flash as of the
    # 2026-08-02 gemini delegation (ROLE_PROVIDERS["review"] =
    # ["openai", "deepseek_native_flash", "deepseek", "claude", "gemini"]).
    for name in ("openai", "deepseek_native_flash", "gemini", "claude", "groq", "openrouter"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    first = ai_router.delegate("Critique this design", task_type="review")["provider"]
    log_result = ai_router.delegate("Check the logs", task_type="log_analysis")["provider"]
    second = ai_router.delegate("Critique this design", task_type="review")["provider"]

    assert [first, second] == ["openai", "deepseek_native_flash"]
    assert log_result == "groq"


def test_delegate_rotation_still_falls_through_to_next_candidate_on_failure(monkeypatch):
    # "review"'s rotation index 1 is deepseek_native_flash as of the
    # 2026-08-02 gemini delegation -- that's the one exercised failing here,
    # not gemini (now last, unreached once claude below succeeds).
    import core.ai_provider as ai_provider

    openai = ai_provider.get_provider("openai")
    monkeypatch.setitem(openai, "available_fn", lambda: True)
    monkeypatch.setitem(openai, "run_text_task", lambda p, timeout=60, project_path=None: "from openai")

    primary = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(primary, "available_fn", lambda: True)

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("deepseek_native_flash down")

    monkeypatch.setitem(primary, "run_text_task", boom)

    monkeypatch.setitem(ai_provider.get_provider("deepseek"), "available_fn", lambda: False)
    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined "review" --
    # disabled so the fallback walk still reaches claude, not gemini, here.
    monkeypatch.setitem(ai_provider.get_provider("gemini"), "available_fn", lambda: False)
    # 17Z: qwen3_coder_text and geminix disabled so the fallback reaches claude.
    monkeypatch.setitem(ai_provider.get_provider("geminix"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("qwen3_coder_text"), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "from claude")

    # First call rotates to "openai" (index 0) and succeeds there.
    first = ai_router.delegate("Critique this design", task_type="review")["provider"]
    # Second call rotates its starting point to "deepseek_native_flash",
    # which fails -- fallback must still walk forward past unavailable
    # deepseek to "claude", not raise.
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

    for name in ("openrouter_claude_opus", "opencode_claude", "openrouter_claude_sonnet",
                 "opencode_claude_sonnet", "opencode_claude_opus"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

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

    for name in ("openrouter_claude_opus", "opencode_claude", "openrouter_claude_sonnet",
                 "opencode_claude_sonnet", "opencode_claude_opus"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

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
    #
    # Was pinned to mocking "gemini" available/succeeding, but gemini was
    # removed from every ROLE_PROVIDERS list 2026-08-02 (see
    # test_gemini_disabled_deepseek_native_flash_took_its_slot_2026_08_02)
    # -- the mock was doing nothing, and this test only passed by accident
    # (real network/credentials on whichever real candidate happened to be
    # first). Mock the role's actual primary candidate directly instead of
    # depending on ambient environment state.
    import core.ai_provider as ai_provider

    deepseek_native_flash = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(deepseek_native_flash, "available_fn", lambda: True)
    monkeypatch.setitem(deepseek_native_flash, "run_text_task", lambda p, timeout=60, project_path=None: "planned")

    ai_router.delegate("Design an application architecture", task_type="planning")

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

    # opencode_claude gained a real text_task route 2026-08-02 and sits
    # ahead of "deepseek" in "planning" -- disabled so this test doesn't
    # make a real opencode/Zen call.
    for name in ("gemini", "openrouter", "deepseek_native_flash", "opencode_claude", "deepseek_native_pro", "geminix"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    deepseek = ai_provider.get_provider("deepseek")
    monkeypatch.setitem(deepseek, "available_fn", lambda: True)
    monkeypatch.setitem(deepseek, "run_text_task", lambda p, timeout=60, project_path=None: "deepseek planned")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "deepseek"


def test_classification_role_prefers_groq_and_falls_back_when_unavailable(monkeypatch):
    # 2026-07-31: new task_type for intent detection / request classification
    # / structured extraction -- short, fast, low-reasoning-depth calls groq
    # suits well, distinct from "planning" which core.api._extract_build_intent
    # used to (mis)use for exactly this kind of call.
    import core.ai_provider as ai_provider

    assert ai_router.ROLE_PROVIDERS["classification"][0] == "groq"

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task", lambda p, timeout=60, project_path=None: "groq classified")

    result = ai_router.delegate("Classify this request", task_type="classification")
    assert result["provider"] == "groq"


def test_classification_role_falls_back_to_claude_when_groq_has_no_credentials(monkeypatch):
    # groq has no recorded usage in this system at all (no GROQ_API_KEY
    # configured) -- must fail closed to a real, already-proven fallback,
    # not raise, when it's simply not registered with credentials. gemini
    # removed from "classification" entirely 2026-08-02 -- claude (now
    # last) demonstrates the fallback instead.
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("groq"), "available_fn", lambda: False)
    # gemini re-enabled 2026-08-02 (credit reloaded) and rejoined
    # "classification" -- disabled so this still exercises claude as the
    # fallback.
    for name in ("deepseek_native_flash", "deepseek", "gemini", "geminix", "qwen3_coder_text"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude classified")

    result = ai_router.delegate("Classify this request", task_type="classification")
    assert result["provider"] == "claude"


def test_delegate_documentation_task_includes_deepseek_as_a_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ("gemini", "groq", "openrouter", "deepseek_native_flash"):
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
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)

    deepseek = ai_provider.get_provider("deepseek")
    monkeypatch.setitem(deepseek, "available_fn", lambda: True)
    monkeypatch.setitem(deepseek, "run_text_task", lambda p, timeout=60, project_path=None: "deepseek reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "deepseek"


@pytest.mark.parametrize("role", ["coding"])
def test_openrouter_billed_coding_routes_disabled_2026_08_02(role):
    # Operator directive 2026-08-02: the OpenRouter account is out of
    # credit. openrouter_claude_opus, openrouter_claude_sonnet, and
    # opencode_deepseek (billed through that same account, the last via
    # opencode's own stored OpenRouter credential) are removed from the
    # coding role entirely -- still registered in core.ai_provider, so
    # re-adding them is a one-line change once the account's credit clears.
    candidates = ai_router.ROLE_PROVIDERS[role]
    assert "openrouter_claude_opus" not in candidates
    assert "openrouter_claude_sonnet" not in candidates
    assert "opencode_deepseek" not in candidates


# --- 13M: Claude-preserving coding order + coding front rotation ------------
# 2026-08-03 operator directive ("disable open code, assign jobs to qwen3"):
# qwen3_coding is now the sole front-group member. opencode_claude moved to
# the fixed tail as a fallback (Zen account quota_exceeded).
# See ROLE_PROVIDERS["coding"]'s comment and ai_router.CODING_ROTATING_FRONT.

CODING_FIXED_TAIL = [
    "opencode_claude",
    "opencode_claude_sonnet",
    "opencode_claude_opus",
    # 2026-08-03: OmniRoute (localhost:20128) sits ahead of the degraded
    # CloudCLI "claude" provider as Kai's always-on fallback -- claude hangs
    # on its untrusted-workspace/out-of-credit state instead of failing fast,
    # while omniroute aggregates healthy upstreams (see ROLE_PROVIDERS'
    # comment and the omniroute provider registration in ai_provider.py).
    "omniroute",
    "claude",
    "opencode",
    "opencode_minimax",
]


def test_coding_rotating_front_is_qwen3_coding_2026_08_03():
    # 2026-08-03 operator directive ("disable open code for now, assign jobs
    # to qwen3"): qwen3_coding is now the sole front-group member and primary
    # coding provider. opencode_claude (Fable 5) moved to the fixed tail as a
    # fallback — Zen account quota_exceeded 2026-08-03. qwen3 is the only
    # route with real capacity (self-hosted RunPod RTX 5090).
    assert ai_router.CODING_ROTATING_FRONT == ["qwen3_coding"]
    assert ai_router.ROLE_PROVIDERS["coding"][:1] == ai_router.CODING_ROTATING_FRONT
    # OmniRoute must sit ahead of the degraded direct claude provider as the
    # always-on fallback (2026-08-03 operator directive).
    coding = ai_router.ROLE_PROVIDERS["coding"]
    assert "omniroute" in coding
    assert coding.index("omniroute") < coding.index("claude")


def test_candidates_for_coding_rotates_only_the_alt_claude_front_group():
    candidates = ai_router._candidates_for("coding")

    assert len(candidates) == len(ai_router.ROLE_PROVIDERS["coding"])
    assert candidates[:1] == ai_router.CODING_ROTATING_FRONT
    assert candidates[1:] == CODING_FIXED_TAIL


def test_candidates_for_coding_front_order_rotates_while_the_tail_never_changes():
    # With a single-member front group, "rotation" is a no-op -- every call
    # returns the same order. The tail must still never change.
    fronts, tails = [], []
    for _ in range(4):
        candidates = ai_router._candidates_for("coding")
        fronts.append(candidates[:1])
        tails.append(candidates[1:])

    front = ai_router.CODING_ROTATING_FRONT
    assert all(f == front for f in fronts)
    assert all(tail == CODING_FIXED_TAIL for tail in tails)


def test_direct_claude_is_never_first_for_coding():
    # The whole point of 13M's coding order: the direct Claude/Anthropic
    # subscription is a last-resort fallback, never the first attempt.
    for _ in range(6):
        assert ai_router._candidates_for("coding")[0] != "claude"


def test_candidates_for_coding_respects_an_overridden_role_list(monkeypatch):
    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]}
    )

    # With no rotating-front members present, the overridden list is used
    # verbatim (and repeatedly -- nothing rotates).
    assert ai_router._candidates_for("coding") == ["claude", "opencode"]
    assert ai_router._candidates_for("coding") == ["claude", "opencode"]


def test_candidates_for_non_coding_roles_is_unchanged_and_unrotated():
    for role in ("planning", "log_analysis", "documentation", "review", "architecture"):
        assert ai_router._candidates_for(role) == ai_router.ROLE_PROVIDERS[role]


def test_delegate_does_not_double_rotate_the_coding_candidates(monkeypatch):
    import core.ai_provider as ai_provider

    rotate_calls = []
    real_rotate = ai_router._rotate_candidates

    def spying_rotate(task_type, candidates):
        rotate_calls.append(list(candidates))
        return real_rotate(task_type, candidates)

    monkeypatch.setattr(ai_router, "_rotate_candidates", spying_rotate)

    for name in ai_router.ROLE_PROVIDERS["coding"]:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    # Exactly one rotation -- the front group inside _candidates_for. The
    # outer per-role rotation in delegate() must not wrap it a second time.
    assert rotate_calls == [ai_router.CODING_ROTATING_FRONT]


def test_delegate_coding_falls_through_the_fixed_tail_in_order_when_alt_claude_routes_are_down(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ai_router.CODING_ROTATING_FRONT + ["opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus"]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(
        claude, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
        return_attempts=True,
    )

    # With every alt-Claude route unavailable, direct claude is reached --
    # and only after every candidate ahead of it (the rotating front, the
    # rest of the Claude family, qwen3, and the OmniRoute always-on gateway)
    # was attempted/skipped first.
    assert result["provider"] == "claude"
    attempted_before_claude = [a["provider"] for a in result["attempts"]]
    assert attempted_before_claude[:1] == ai_router.CODING_ROTATING_FRONT
    # 2026-08-03: qwen3_coding is now the front group. opencode_claude moved
    # to the fixed tail.  The fixed tail order is: opencode_claude →
    # opencode_claude_sonnet → opencode_claude_opus → omniroute → claude → ...
    assert attempted_before_claude[1:] == [
        "opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus", "omniroute",
    ]


def test_delegate_coding_falls_all_the_way_to_opencode_when_claude_is_also_down(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ai_router.CODING_ROTATING_FRONT + [
        "opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus", "claude", "opencode_deepseek",
    ]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    opencode = ai_provider.get_provider("opencode")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(
        opencode, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
    )

    assert result["provider"] == "opencode"


def test_delegate_coding_raises_all_providers_failed_when_every_candidate_is_down(monkeypatch):
    import core.ai_provider as ai_provider

    for name in ai_router.ROLE_PROVIDERS["coding"]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    with pytest.raises(AllProvidersFailed) as excinfo:
        ai_router.delegate("Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent")

    attempted = [a["provider"] for a in excinfo.value.attempts]
    assert sorted(attempted) == sorted(ai_router.ROLE_PROVIDERS["coding"])


def test_delegate_coding_always_picks_the_sole_front_candidate(monkeypatch):
    # 2026-08-02: qwen3_coding is CODING_ROTATING_FRONT's sole member again
    # (see test_coding_rotating_front_is_qwen3_alone_now_2026_08_02) -- with
    # nothing else to rotate across, every call lands on the same provider.
    import core.ai_provider as ai_provider

    front = ai_router.CODING_ROTATING_FRONT
    for name in front:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(
            provider, "run_coding_task",
            lambda project_path, instruction, n=name, **kwargs: {"success": True, "response_text": f"from {n}", "files_changed": [], "commits": [], "tool_errors": []},
        )

    seen = [
        ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")["provider"]
        for _ in range(4)
    ]

    assert seen == [front[0]] * 4


@pytest.mark.parametrize("role", ["planning", "log_analysis", "documentation", "review"])
def test_non_coding_roles_do_not_include_the_openrouter_coding_routes(role):
    assert "openrouter_claude_opus" not in ai_router.ROLE_PROVIDERS[role]
    assert "openrouter_claude_sonnet" not in ai_router.ROLE_PROVIDERS[role]


def test_13v_architecture_chain_candidates_all_resolve_with_text_capability():
    # The 13V Chief Architect chain needs every one of its candidates to be
    # a real text_task provider. openrouter_claude was dropped 2026-08-02
    # (OpenRouter account out of credit, same operator directive that hit
    # the coding role) -- see test_openrouter_claude_disabled_..._2026_08_02
    # for the removal itself; this test just guards the remaining chain.
    import core.ai_provider as ai_provider

    assert "openrouter_claude" not in ai_router.ROLE_PROVIDERS["architecture"]
    for name in ai_router.ROLE_PROVIDERS["architecture"]:
        provider = ai_provider.get_provider(name)
        assert provider is not None, name
        assert provider.get("run_text_task") is not None, name


def test_openrouter_claude_disabled_deepseek_native_flash_took_its_slot_2026_08_02():
    # Operator directive 2026-08-02: the OpenRouter account is out of
    # credit (same directive that disabled the coding role's OpenRouter
    # routes). openrouter_claude is removed from the architecture chain
    # entirely -- still registered in core.ai_provider, one-line re-add
    # once the account's credit clears. Direct "claude" is also out of
    # credit right now but stays in the tail rather than being removed
    # (the operator asked to reroute its work, not delete the route), so
    # deepseek_native_flash takes the primary slot in the meantime.
    candidates = ai_router.ROLE_PROVIDERS["architecture"]
    assert candidates[0] == "deepseek_native_flash"
    assert "openrouter_claude" not in candidates
    assert "claude" in candidates


@pytest.mark.parametrize("role", ["documentation", "law_case_analysis", "law_teaching"])
def test_gemini_was_never_routed_to_these_roles(role):
    # Unlike planning/architecture/review/classification/law_document below,
    # gemini was never a candidate here (documentation/law_case_analysis/
    # law_teaching have their own designated primaries -- deepseek_native_flash/
    # claude/openai respectively) -- so its 2026-08-02 disable-then-re-enable
    # cycle never touched these lists at all.
    candidates = ai_router.ROLE_PROVIDERS[role]
    assert "gemini" not in candidates


@pytest.mark.parametrize("role", ["planning", "architecture", "review", "classification", "law_document"])
def test_gemini_reenabled_after_credit_reload_2026_08_02(role):
    # Operator directive 2026-08-02: gemini was quota_exceeded (429, Google
    # billing, unrelated to accuracy) -- deepseek_native_flash (native
    # api.deepseek.com, no shared-quota exposure) took over its slot.
    # Initially just deprioritized (moved to last); operator then directed
    # disabling it outright ("disable gemini for now") after confirming an
    # 18-phase build pileup traced to this exact gemini/openrouter quota
    # wall -- removed from every role's candidate list entirely.
    #
    # Re-enabled later the same day ("gemini credit has been reloaded") --
    # restored to each role per its original evidence-based position (see
    # each role's own comment in ROLE_PROVIDERS/LAW_TUTOR_ROLE_PROVIDERS).
    # deepseek_native_flash and everything else added while gemini was out
    # stays in the list too -- gemini's return didn't remove anything.
    # ROLE_PROVIDERS.update(LAW_TUTOR_ROLE_PROVIDERS) merges the law_* roles
    # into the same dict at module load, so law_document is reachable here too.
    candidates = ai_router.ROLE_PROVIDERS[role]
    assert "gemini" in candidates
    assert "deepseek_native_flash" in candidates


# 17Z: qwen3_coder_text (self-hosted RunPod RTX 5090) -- must appear in every
# text-task role as fallback capacity behind primaries, ahead of the universal
# "claude" tail, and must never displace or appear ahead of any primary.
TEXT_ROLES_WITH_QWEN3 = [
    "planning",
    "architecture",
    "log_analysis",
    "documentation",
    "review",
    "classification",
    "law_document",
    "law_case_analysis",
    "law_teaching",
    "law_exam",
    "law_flashcards",
    "law_chat",
]


@pytest.mark.parametrize("role", TEXT_ROLES_WITH_QWEN3)
def test_qwen3_coder_text_appears_in_fallback_position_in_text_roles(role):
    candidates = ai_router.ROLE_PROVIDERS[role]
    assert "qwen3_coder_text" in candidates, f"qwen3_coder_text missing from {role}: {candidates}"


def test_qwen3_coder_text_not_in_coding_role():
    # qwen3_coder_text is text-task only (OpenAI-compatible chat-completions,
    # no tool-use loop). The coding role uses qwen3_coding instead.
    assert "qwen3_coder_text" not in ai_router.ROLE_PROVIDERS["coding"]


@pytest.mark.parametrize("role", TEXT_ROLES_WITH_QWEN3)
def test_qwen3_coder_text_never_displaces_known_primaries(role):
    # 17Z/17M rule: this is fallback capacity, not a replacement.
    # It must appear AFTER every known primary for its role, never ahead of
    # or displacing them.
    candidates = ai_router.ROLE_PROVIDERS[role]
    qwen3_idx = candidates.index("qwen3_coder_text")

    known_primaries = {
        "planning": ["gemini", "geminix", "deepseek_native_flash"],
        "architecture": ["deepseek_native_flash", "gemini"],
        "log_analysis": ["groq"],
        "documentation": ["deepseek_native_flash", "groq"],
        "review": ["openai", "deepseek_native_flash"],
        "classification": ["groq", "deepseek_native_flash"],
        "law_document": ["gemini", "geminix"],
        "law_case_analysis": ["claude", "deepseek_native_flash"],
        "law_teaching": ["openai", "claude"],
        "law_exam": ["claude", "openai"],
        "law_flashcards": ["deepseek", "groq"],
        "law_chat": ["groq", "deepseek"],
    }

    for primary in known_primaries.get(role, []):
        primary_idx = candidates.index(primary)
        assert primary_idx < qwen3_idx, (
            f"{role}: qwen3_coder_text at index {qwen3_idx} but primary "
            f"{primary} is at index {primary_idx} -- fallback must not "
            f"displace or appear ahead of any primary"
        )


def test_qwen3_coder_text_before_claude_in_tail_position():
    # In roles where claude is the universal last-resort tail (the last
    # candidate in the list), qwen3_coder_text should be tried before it
    # so the paid GPU capacity gets used before the depleted Anthropic
    # subscription. Law roles (law_*) position claude mid-chain
    # intentionally (e.g., law_flashcards: deepseek -> groq -> claude -> ...),
    # so this assertion only covers the non-law operational text roles.
    claude_tail_roles = [
        "planning", "architecture", "log_analysis", "documentation",
        "review", "classification",
    ]
    for role in claude_tail_roles:
        candidates = ai_router.ROLE_PROVIDERS[role]
        assert "claude" in candidates
        qwen3_idx = candidates.index("qwen3_coder_text")
        claude_idx = candidates.index("claude")
        assert qwen3_idx < claude_idx, (
            f"{role}: qwen3_coder_text ({qwen3_idx}) must be tried before "
            f"claude ({claude_idx})"
        )
