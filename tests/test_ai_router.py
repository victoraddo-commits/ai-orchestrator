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
    # 2026-08-07: planning chain = deepseek_native_flash, deepseek_native_pro,
    # gemini, ... -- gemini is 3rd, reached after the native deepseek providers
    # are disabled.
    ("Design an application architecture", "gemini"),
    # 2026-08-07: log_analysis chain = deepseek_native_flash, groq, ... --
    # groq is 2nd, reached after deepseek_native_flash is disabled.
    ("Analyze Docker error log", "groq"),
    # "Build authentication system" → "claude" case removed 2026-08-07: claude
    # is no longer in the coding chain (out of credit, removed).
])
def test_delegate_routes_to_expected_provider(monkeypatch, description, expected_provider):
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 (Fable 5 Q&A,
    # see ai_provider._opencode_claude_run_text_task) and now leads both
    # "coding" and "planning" -- disabled here so this stays a fast,
    # network-free unit test rather than a real opencode/Zen call.
    # deepseek_native_pro joined "planning" the same day (real network call
    # via api.deepseek.com if left available) -- disabled for the same reason.
    # qwen4_coding joined CODING_ROTATING_FRONT as primary coding provider
    # 2026-08-03 (real opencode CLI call via RunPod if left available).
    # 2026-08-07: qwen4_coding/qwen4_text removed — RunPod pods decommissioned.
    # Disable every provider except the one we expect so the delegate call
    # is forced to hit it.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude",
                 "deepseek_native_pro", "opencode_claude_sonnet",
                 "opencode_claude_opus", "omniroute", "opencode", "opencode_minimax"):
        provider = ai_provider.get_provider(name)
        if provider is not None:
            monkeypatch.setitem(provider, "available_fn", lambda: False)

    for name in ("claude", "gemini", "groq"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        if provider.get("run_text_task"):
            monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"response from {n}")

    result = ai_router.delegate(description)

    assert result["provider"] == expected_provider


def test_delegate_documentation_task_accepts_gemini_or_groq(monkeypatch):
    import core.ai_provider as ai_provider

    # deepseek_native_flash and omniroute_deepseek_flash now sit ahead of
    # groq/gemini in "documentation" -- disable them so this test still
    # exercises the groq/gemini choice it's named for.
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("omniroute_deepseek_flash"), "available_fn", lambda: False)

    for name in ("claude", "gemini", "groq"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        if provider.get("run_text_task"):
            monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"response from {n}")

    result = ai_router.delegate("Generate README documentation")

    assert result["provider"] in ("gemini", "groq")


def test_delegate_falls_back_when_first_choice_unavailable(monkeypatch):
    import core.ai_provider as ai_provider

    # Disable all "planning" chain members + add claude as last resort.
    for name in ("deepseek_native_flash", "gemini", "openrouter", "deepseek", "minimax",
                 "opencode_claude", "deepseek_native_pro", "geminix", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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

    # Disable remaining planning candidates so claude (appended below) answers.
    for name in ("gemini", "openrouter", "deepseek", "minimax", "opencode_claude",
                 "deepseek_native_pro", "geminix", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "claude"


def test_delegate_raises_when_every_candidate_fails(monkeypatch):
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 -- included
    # here so every "planning" candidate really is unavailable.
    for name in ("deepseek_native_flash", "gemini", "openrouter", "deepseek", "minimax", "claude", "opencode_claude", "deepseek_native_pro", "geminix", "omniroute_deepseek_flash"):
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
    monkeypatch.setitem(claude, "enabled", True)
    monkeypatch.setitem(claude, "available_fn", lambda: True)
    monkeypatch.setitem(claude, "run_text_task", lambda p, timeout=60, project_path=None: "claude answered")

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "minimax", "opencode_claude",
                 "deepseek_native_pro", "geminix", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

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
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude",
                 "deepseek_native_pro", "gemini", "geminix", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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

    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude",
                 "deepseek_native_pro", "gemini", "geminix", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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
    for name in ("openrouter", "deepseek", "minimax", "gemini", "geminix",
                 "opencode_claude", "deepseek_native_pro", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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

    # 2026-08-07: coding chain = opencode_claude → opencode_claude_sonnet →
    # opencode_claude_opus → omniroute → opencode → opencode_minimax.
    # This test overrides to ["claude"] for simplicity -- the aim is exactly
    # to verify that capability=coding_agent calls run_coding_task, not
    # run_text_task, regardless of which provider answers.
    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["claude"]},
    )

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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
    monkeypatch.setitem(claude, "enabled", True)
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

    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
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
    monkeypatch.setitem(claude, "enabled", True)
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
    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    snapshot = provider_health.get_quota_snapshot("claude")
    assert snapshot["status"] == "quota_exceeded"
    assert "weekly limit" in snapshot["detail"].lower()


def test_delegate_records_a_generic_coding_failure_as_error_not_quota_exceeded(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_health as provider_health

    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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
    monkeypatch.setitem(claude, "enabled", True)
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
    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
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

    # Disable deepseek_native_flash (first in log_analysis) so groq answers.
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task", lambda p, timeout=60, project_path=None: "forced")

    result = ai_router.delegate("some ambiguous text", task_type="log_analysis")

    assert result["provider"] == "groq"
    assert result["task_type"] == "log_analysis"


def test_delegate_review_task_type_routes_to_primary(monkeypatch):
    import core.ai_provider as ai_provider

    primary = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", lambda p, timeout=60, project_path=None: "reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "deepseek_native_flash"


def test_delegate_review_task_type_falls_back_to_last_resort(monkeypatch):
    # 2026-08-07: review chain = deepseek_native_flash -> omniroute_deepseek_flash
    # -> gemini -> geminix. Disable all but the last (geminix).
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("omniroute_deepseek_flash"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("gemini"), "available_fn", lambda: False)

    last = ai_provider.get_provider("geminix")
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(last, "run_text_task", lambda p, timeout=60, project_path=None: "geminix reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "geminix"


def test_get_provider_dashboard_summarizes_last_request_per_provider(monkeypatch):
    # gemini removed from "planning" entirely 2026-08-02 -- claude (now
    # last) demonstrates the dashboard summary instead. gemini itself stays
    # a registered provider (still listed in the dashboard, see the
    # dedicated test below), just never routed to right now.
    import core.ai_provider as ai_provider

    # opencode_claude gained a real text_task route 2026-08-02 -- disabled
    # so this test doesn't make a real opencode/Zen call.
    for name in ("deepseek_native_flash", "openrouter", "deepseek", "opencode_claude",
                 "deepseek_native_pro", "gemini", "geminix", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {
        **ai_router.ROLE_PROVIDERS,
        "planning": ai_router.ROLE_PROVIDERS["planning"] + ["claude"],
    })

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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
    # first -- rotation gives every role candidate a real turn as the first
    # attempt so idle credits actually get used. ("review" is used here
    # rather than "planning" because planning is now FIXED_ORDER.)
    import core.ai_provider as ai_provider

    # 2026-08-07: review chain = deepseek_native_flash -> omniroute_deepseek_flash
    # -> gemini -> geminix.
    review_candidates = ai_router.ROLE_PROVIDERS["review"]
    for name in review_candidates:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    # Call n+1 times (rotate through all candidates once, wrapping back to
    # the first entry on the final call).
    seen = [ai_router.delegate("Critique this design", task_type="review")["provider"] for _ in range(len(review_candidates) + 1)]

    assert seen == review_candidates + [review_candidates[0]]


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

    # 2026-08-07: review = ["deepseek_native_flash", "omniroute_deepseek_flash",
    # "gemini", "geminix"], log_analysis = ["deepseek_native_flash", "groq",
    # "omniroute_deepseek_flash"].
    for name in ("deepseek_native_flash", "omniroute_deepseek_flash", "gemini", "geminix", "groq"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)
        monkeypatch.setitem(provider, "run_text_task", lambda p, timeout=60, project_path=None, n=name: f"from {n}")

    first = ai_router.delegate("Critique this design", task_type="review")["provider"]
    log_result = ai_router.delegate("Check the logs", task_type="log_analysis")["provider"]
    second = ai_router.delegate("Critique this design", task_type="review")["provider"]

    review_chain = ai_router.ROLE_PROVIDERS["review"]
    assert [first, second] == [review_chain[0], review_chain[1]]
    assert log_result == ai_router.ROLE_PROVIDERS["log_analysis"][0]


def test_delegate_rotation_still_falls_through_to_next_candidate_on_failure(monkeypatch):
    # 2026-08-07: review chain = deepseek_native_flash -> omniroute_deepseek_flash
    # -> gemini -> geminix. First call lands on deepseek_native_flash (index 0),
    # second rotates to omniroute_deepseek_flash which fails, fallback to gemini.
    import core.ai_provider as ai_provider

    first_primary = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(first_primary, "available_fn", lambda: True)
    monkeypatch.setitem(first_primary, "run_text_task", lambda p, timeout=60, project_path=None: "from deepseek_native_flash")

    second_primary = ai_provider.get_provider("omniroute_deepseek_flash")
    monkeypatch.setitem(second_primary, "available_fn", lambda: True)

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("omniroute_deepseek_flash down")

    monkeypatch.setitem(second_primary, "run_text_task", boom)

    monkeypatch.setitem(ai_provider.get_provider("geminix"), "available_fn", lambda: False)

    fallback = ai_provider.get_provider("gemini")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task", lambda p, timeout=60, project_path=None: "from gemini")

    # First call rotates to index 0 (deepseek_native_flash) and succeeds.
    first = ai_router.delegate("Critique this design", task_type="review")["provider"]
    # Second call rotates to index 1 (omniroute_deepseek_flash), which fails
    # -- fallback walks forward to gemini.
    second = ai_router.delegate("Critique this design", task_type="review")["provider"]

    assert [first, second] == ["deepseek_native_flash", "gemini"]


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

    # Override coding chain to use claude directly for this cost-recording test.
    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["claude"]},
    )

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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

    # Override coding chain to use claude directly for this no-cost test.
    monkeypatch.setattr(
        ai_router, "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["claude"]},
    )

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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

    # Override to force the exact coding chain we want. Also disable
    # CODING_ROTATING_FRONT's opencode_claude so it doesn't get prepended.
    monkeypatch.setattr(ai_router, "CODING_ROTATING_FRONT", [])
    monkeypatch.setattr(ai_router, "ROLE_PROVIDERS", {**ai_router.ROLE_PROVIDERS, "coding": ["claude", "opencode"]})

    claude = ai_provider.get_provider("claude")
    monkeypatch.setitem(claude, "enabled", True)
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


def test_minimax_coding_agent_route_is_not_ahead_of_the_family():
    # "observe", not "trusted": 3 attempts is below MIN_SAMPLE_SIZE, so it
    # earns a place in the rotation, not the front of it.
    # 2026-08-07: direct "claude" is no longer in the coding chain (out of
    # credit). opencode_claude (Fable 5) is the primary.
    coding = ai_router.ROLE_PROVIDERS["coding"]

    assert coding.index("opencode_minimax") > coding.index("opencode_claude")


def test_coding_role_still_ends_on_opencode_fallback():
    # 2026-08-07: direct "claude" (CloudCLI/Anthropic subscription, out of
    # credit) is no longer in the coding chain. opencode_claude (Fable 5,
    # healthy) is the primary — and the Claude-family universal fallback.
    assert "opencode_claude" in ai_router.ROLE_PROVIDERS["coding"]
    assert "opencode" in ai_router.ROLE_PROVIDERS["coding"]


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

    # 2026-08-07: coding chain is opencode_claude -> opencode_claude_sonnet ->
    # opencode_claude_opus -> omniroute -> opencode -> opencode_minimax
    for name in ("opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus", "omniroute", "opencode", "opencode_minimax"):
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: True)

    def fail(project_path, instruction, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setitem(ai_provider.get_provider("opencode_claude"), "run_coding_task", fail)
    monkeypatch.setitem(ai_provider.get_provider("opencode_claude_sonnet"), "run_coding_task", fail)
    monkeypatch.setitem(ai_provider.get_provider("opencode_claude_opus"), "run_coding_task", fail)
    monkeypatch.setitem(ai_provider.get_provider("omniroute"), "run_coding_task", fail)
    monkeypatch.setitem(ai_provider.get_provider("opencode"), "run_coding_task", fail)
    monkeypatch.setitem(
        ai_provider.get_provider("opencode_minimax"),
        "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "done"},
    )
    monkeypatch.setattr(
        ai_router,
        "ROLE_PROVIDERS",
        {**ai_router.ROLE_PROVIDERS, "coding": ["opencode_claude", "opencode_claude_sonnet", "opencode_claude_opus", "omniroute", "opencode", "opencode_minimax"]},
    )

    result = ai_router.delegate("Build a widget", capability="coding_agent", project_path="/tmp/x")

    assert result["provider"] == "opencode_minimax"


# --- 13U: deepseek text-task + opencode_deepseek coding-agent routing ------

def test_delegate_planning_task_includes_deepseek_as_a_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    # 2026-08-07: "deepseek" (OpenRouter-proxied) is no longer in any chain.
    # Test omniroute_deepseek_flash instead -- it IS in the planning chain.
    for name in ("deepseek_native_flash", "deepseek_native_pro", "gemini", "geminix", "opencode_claude"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    odf = ai_provider.get_provider("omniroute_deepseek_flash")
    monkeypatch.setitem(odf, "available_fn", lambda: True)
    monkeypatch.setitem(odf, "run_text_task", lambda p, timeout=60, project_path=None: "omniroute_deepseek_flash planned")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "omniroute_deepseek_flash"


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
    # "classification" -- disabled so this still exercises geminix as the
    # fallback.
    for name in ("deepseek_native_flash", "deepseek", "gemini", "omniroute_deepseek_flash"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    last = ai_provider.get_provider("geminix")
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(last, "run_text_task", lambda p, timeout=60, project_path=None: "geminix classified")

    result = ai_router.delegate("Classify this request", task_type="classification")
    assert result["provider"] == "geminix"


def test_delegate_documentation_task_includes_omniroute_deepseek_flash(monkeypatch):
    import core.ai_provider as ai_provider

    # 2026-08-07: documentation chain = deepseek_native_flash, omniroute_deepseek_flash, groq.
    for name in ("deepseek_native_flash", "groq"):
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    odf = ai_provider.get_provider("omniroute_deepseek_flash")
    monkeypatch.setitem(odf, "available_fn", lambda: True)
    monkeypatch.setitem(odf, "run_text_task", lambda p, timeout=60, project_path=None: "omniroute_deepseek_flash documented")

    result = ai_router.delegate("Generate README documentation")

    assert result["provider"] == "omniroute_deepseek_flash"


def test_delegate_review_task_includes_omniroute_deepseek_flash_as_candidate(monkeypatch):
    import core.ai_provider as ai_provider

    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("gemini"), "available_fn", lambda: False)
    monkeypatch.setitem(ai_provider.get_provider("geminix"), "available_fn", lambda: False)

    omniroute = ai_provider.get_provider("omniroute_deepseek_flash")
    monkeypatch.setitem(omniroute, "available_fn", lambda: True)
    monkeypatch.setitem(omniroute, "run_text_task", lambda p, timeout=60, project_path=None: "omniroute reviewed")

    result = ai_router.delegate("Critique this design", task_type="review")

    assert result["provider"] == "omniroute_deepseek_flash"


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
# 2026-08-07 operator directive: qwen4_coding deregistered (RunPod pods
# decommissioned). opencode_claude (Fable 5 via OpenCode Zen, separate
# billing, healthy) is now the sole front-group member and primary coding
# provider. Direct "claude" (CloudCLI/Anthropic subscription, out of credit)
# is no longer in the coding chain.
# See ROLE_PROVIDERS["coding"]'s comment and ai_router.CODING_ROTATING_FRONT.

CODING_FIXED_TAIL = [
    "opencode_claude_sonnet",
    "opencode_claude_opus",
    "omniroute",
    "opencode",
    "opencode_minimax",
]


def test_coding_rotating_front_is_opencode_claude_2026_08_07():
    # 2026-08-07 operator directive: qwen4_coding deregistered (RunPod pods
    # decommissioned). opencode_claude (Fable 5 via OpenCode Zen, separate
    # billing, healthy) returned as the sole front-group member and primary
    # coding provider.
    assert ai_router.CODING_ROTATING_FRONT == ["opencode_claude"]
    assert ai_router.ROLE_PROVIDERS["coding"][:1] == ai_router.CODING_ROTATING_FRONT
    # OmniRoute must sit ahead of opencode (degraded) as the always-on
    # fallback (2026-08-07).
    coding = ai_router.ROLE_PROVIDERS["coding"]
    assert "omniroute" in coding
    assert coding.index("omniroute") < coding.index("opencode")


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

    # 2026-08-07: coding chain = opencode_claude -> opencode_claude_sonnet ->
    # opencode_claude_opus -> omniroute -> opencode -> opencode_minimax.
    # Disable all except the last one (opencode_minimax).
    for name in ai_router.ROLE_PROVIDERS["coding"][:-1]:
        provider = ai_provider.get_provider(name)
        monkeypatch.setitem(provider, "available_fn", lambda: False)

    last = ai_provider.get_provider("opencode_minimax")
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(
        last, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    ai_router.delegate("Implement", task_type="coding", project_path="/proj", capability="coding_agent")

    # Exactly one rotation -- the front group inside _candidates_for. The
    # outer per-role rotation in delegate() must not wrap it a second time.
    assert rotate_calls == [ai_router.CODING_ROTATING_FRONT]


def test_delegate_coding_falls_through_the_fixed_tail_in_order_when_front_routes_are_down(monkeypatch):
    import core.ai_provider as ai_provider

    # 2026-08-07: coding chain = front (opencode_claude) + tail (opencode_claude_sonnet,
    # opencode_claude_opus, omniroute, opencode, opencode_minimax). Disable the
    # front and the first few tail entries.
    for name in ai_router.CODING_ROTATING_FRONT + ["opencode_claude_sonnet", "opencode_claude_opus", "omniroute"]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    last = ai_provider.get_provider("opencode")
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(
        last, "run_coding_task",
        lambda project_path, instruction, **kwargs: {"success": True, "response_text": "ok", "files_changed": [], "commits": [], "tool_errors": []},
    )

    result = ai_router.delegate(
        "Implement the widget", task_type="coding", project_path="/proj", capability="coding_agent",
        return_attempts=True,
    )

    # With every candidate ahead of opencode unavailable, opencode is reached
    # after the rotating front and the full tail order.
    assert result["provider"] == "opencode"
    attempted_before = [a["provider"] for a in result["attempts"]]
    assert attempted_before[:1] == ai_router.CODING_ROTATING_FRONT
    assert attempted_before[1:] == ["opencode_claude_sonnet", "opencode_claude_opus", "omniroute"]


def test_delegate_coding_falls_all_the_way_to_opencode_when_front_routes_are_down(monkeypatch):
    import core.ai_provider as ai_provider

    # 2026-08-07: coding chain = opencode_claude -> opencode_claude_sonnet ->
    # opencode_claude_opus -> omniroute -> opencode -> opencode_minimax.
    # Disable all but opencode.
    for name in ai_router.CODING_ROTATING_FRONT + [
        "opencode_claude_sonnet", "opencode_claude_opus", "omniroute",
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
    # 2026-08-07: opencode_claude is CODING_ROTATING_FRONT's sole member --
    # with nothing else to rotate across, every call lands on the same
    # provider (Fable 5 via OpenCode Zen, separate billing, healthy).
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
    # once the account's credit clears. 2026-08-07: architecture primary is
    # now deepseek_native_pro (deepseek_native_flash is second). Direct
    # "claude" is no longer in any chain (out of credit, fully removed).
    candidates = ai_router.ROLE_PROVIDERS["architecture"]
    assert candidates[0] == "deepseek_native_pro"
    assert "openrouter_claude" not in candidates


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


# 2026-08-07: All qwen4 providers (qwen4_coding, qwen4Z, qwen4_text,
# qwen4_pod_b) deregistered -- RunPod pods decommissioned.
# deepseek_native_flash is now the primary fallback across all text roles,
# and opencode_claude is the primary coding provider.


# --- 17R: AI routing resilience ----------------------------------------------
# 1. Native DeepSeek provider verification
# 2. File-access-aware routing
# 3. Wall-clock degraded-state detection
# 4. Circuit-breaker with 60-second cooldown


# --- 17R.1: DeepSeek native provider verification ----------------------------

def test_deepseek_native_pro_is_registered_as_text_task_provider():
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider("deepseek_native_pro")
    assert provider is not None
    assert provider.get("run_text_task") is not None
    assert provider.get("run_coding_task") is None


def test_deepseek_native_flash_is_registered_as_text_task_provider():
    import core.ai_provider as ai_provider

    provider = ai_provider.get_provider("deepseek_native_flash")
    assert provider is not None
    assert provider.get("run_text_task") is not None
    assert provider.get("run_coding_task") is None


def test_deepseek_native_flash_is_primary_in_architecture_and_planning():
    # 2026-08-07: architecture primary is deepseek_native_pro.
    assert ai_router.ROLE_PROVIDERS["architecture"][0] == "deepseek_native_pro"
    # planning primary is deepseek_native_flash
    assert ai_router.ROLE_PROVIDERS["planning"][0] == "deepseek_native_flash"

    # deepseek_native_flash appears in planning as the primary
    assert "deepseek_native_flash" in ai_router.ROLE_PROVIDERS["planning"]


def test_deepseek_native_both_are_separate_from_openrouter_deepseek():
    # Native DeepSeek providers (api.deepseek.com) must be distinct from the
    # OpenRouter-proxied "deepseek" provider -- no shared-quota exposure.
    import core.ai_provider as ai_provider

    for name in ("deepseek_native_pro", "deepseek_native_flash"):
        provider = ai_provider.get_provider(name)
        assert provider is not None
        assert "no OpenRouter/Zen quota exposure" in provider.get("description", "")


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

    # Stub every text-task provider in "planning" except opencode_claude
    # (which has file_access).
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning:
        provider = ai_provider.get_provider(name)
        if name != "opencode_claude":
            monkeypatch.setitem(provider, "available_fn", lambda: False)

    # opencode_claude has file_access in this simplified test setup
    opencode = ai_provider.get_provider("opencode_claude")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(opencode, "run_text_task",
                        lambda p, timeout=60, project_path=None: "opencode_claude with file access")

    result = ai_router.delegate(
        "Read a file and respond", task_type="planning", requires_file_access=True,
    )

    assert result["provider"] == "opencode_claude"


def test_delegate_with_requires_file_access_falls_through_text_providers(monkeypatch):
    import core.ai_provider as ai_provider

    # "review" normally starts with openai (text-only). With requires_file_access,
    # text-only candidates should be skipped.
    planning_order = ai_router.ROLE_PROVIDERS["planning"]
    attempted = []

    for name in planning_order:
        provider = ai_provider.get_provider(name)
        if "file_access" in provider.get("capabilities", []):
            monkeypatch.setitem(provider, "available_fn", lambda: True)
            monkeypatch.setitem(provider, "run_text_task",
                                lambda p, timeout=60, project_path=None, n=name: f"from {n}")
        else:
            monkeypatch.setitem(provider, "available_fn", lambda: True)
            monkeypatch.setitem(provider, "run_text_task",
                                lambda p, timeout=60, project_path=None,
                                n=name, a=attempted: attempted.append(n) or (_ for _ in ()).throw(RuntimeError("text-only")))

    result = ai_router.delegate(
        "Design with file access", task_type="planning", requires_file_access=True,
    )

    # The first file_access-capable provider in "planning" is opencode_claude.
    assert result["provider"] == "opencode_claude"


def test_delegate_without_requires_file_access_does_not_filter(monkeypatch):
    import core.ai_provider as ai_provider

    # Without requires_file_access, text-only providers are used normally.
    # Disable all but the last planning provider (opencode_claude).
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning[:-1]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    last = ai_provider.get_provider("opencode_claude")
    monkeypatch.setitem(last, "available_fn", lambda: True)
    monkeypatch.setitem(last, "run_text_task",
                        lambda p, timeout=60, project_path=None: "opencode_claude text")

    result = ai_router.delegate("Design an application architecture")

    assert result["provider"] == "opencode_claude"


def test_dashboard_includes_file_access_flag():
    import core.ai_provider as ai_provider

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["claude"]["file_access"] is True
    assert dashboard["groq"]["file_access"] is False
    assert dashboard["gemini"]["file_access"] is False


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

    # Disable all planning candidates ahead of opencode_claude so it is reached.
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning:
        if name != "opencode_claude":
            monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    opencode = ai_provider.get_provider("opencode_claude")
    monkeypatch.setitem(opencode, "available_fn", lambda: True)
    monkeypatch.setitem(opencode, "run_text_task",
                        lambda p, timeout=60, project_path=None: "opencode degraded but tried as last resort")

    # Mark opencode_claude as latency-degraded with an extreme spike.
    pl.record_latency("opencode_claude", 100)
    pl.record_latency("opencode_claude", 100)
    pl.record_latency("opencode_claude", 100)
    pl.record_latency("opencode_claude", 5000)
    assert pl.is_latency_degraded("opencode_claude") is True

    # With all other candidates disabled and opencode_claude latency-degraded,
    # it's demoted but tried as last resort -- degradation is demotion, not exclusion.
    result = ai_router.delegate("Design an application architecture", return_attempts=True)

    assert result["provider"] == "opencode_claude"

    degraded_notes = [a for a in result["attempts"] if a["error_type"] == "degraded_health"]
    assert len(degraded_notes) >= 1
    assert all("opencode_claude" in (a.get("provider") or "") for a in degraded_notes)


def test_delegate_records_latency_on_success(monkeypatch):
    import core.ai_provider as ai_provider
    import core.ai.provider_latency as pl

    # Disable all planning candidates except the first (deepseek_native_flash).
    planning = ai_router.ROLE_PROVIDERS["planning"]
    for name in planning[1:]:
        monkeypatch.setitem(ai_provider.get_provider(name), "available_fn", lambda: False)

    primary = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(primary, "available_fn", lambda: True)
    monkeypatch.setitem(primary, "run_text_task", lambda p, timeout=60, project_path=None: "ok")

    ai_router.delegate("Design an application architecture")

    snap = pl.get_latency_snapshot("deepseek_native_flash")
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
    # log_analysis order: deepseek_native_flash, groq, omniroute_deepseek_flash.
    import core.ai.provider_latency as pl
    import core.ai_provider as ai_provider

    # Mark groq as latency-degraded.
    for d in (100, 100, 100, 100):
        pl.record_latency("groq", d)
    pl.record_latency("groq", 5000)

    assert pl.is_latency_degraded("groq") is True

    # Disable omniroute_deepseek_flash so fallback stops before it.
    monkeypatch.setitem(ai_provider.get_provider("omniroute_deepseek_flash"), "available_fn", lambda: False)

    dnf = ai_provider.get_provider("deepseek_native_flash")
    monkeypatch.setitem(dnf, "available_fn", lambda: True)
    monkeypatch.setitem(dnf, "run_text_task",
                        lambda p, timeout=60, project_path=None: "deepseek_native_flash healthy primary")

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task",
                        lambda p, timeout=60, project_path=None: "groq degraded last resort")

    # deepseek_native_flash (healthy) should be tried before groq (degraded)
    result = ai_router.delegate("Analyze Docker error log", task_type="log_analysis",
                                return_attempts=True)

    assert result["provider"] == "deepseek_native_flash"
    assert result["response"] == "deepseek_native_flash healthy primary"


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
        cb.record_failure("groq")

    assert cb.is_open("groq") is True

    # Disable deepseek_native_flash so the open-circuit groq is bypassed and
    # omniroute_deepseek_flash answers.
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task",
                        lambda p, timeout=60, project_path=None: pytest.fail("groq is circuit-open, must be skipped"))

    fallback = ai_provider.get_provider("omniroute_deepseek_flash")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task",
                        lambda p, timeout=60, project_path=None: "omniroute_deepseek_flash to the rescue")

    result = ai_router.delegate("Analyze Docker error log", task_type="log_analysis")

    assert result["provider"] == "omniroute_deepseek_flash"


def test_delegate_records_circuit_breaker_on_failure(monkeypatch):
    import core.ai.circuit_breaker as cb
    import core.ai_provider as ai_provider

    def boom(p, timeout=60, project_path=None):
        raise RuntimeError("connection refused")

    # Disable deepseek_native_flash so groq (which fails) is tried first,
    # then the fallback (omniroute_deepseek_flash) succeeds.
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task", boom)

    fallback = ai_provider.get_provider("omniroute_deepseek_flash")
    monkeypatch.setitem(fallback, "available_fn", lambda: True)
    monkeypatch.setitem(fallback, "run_text_task",
                        lambda p, timeout=60, project_path=None: "omniroute_deepseek_flash saved")

    ai_router.delegate("Analyze Docker error log", task_type="log_analysis")

    snap = cb.get_breaker_snapshot("groq")
    assert snap["consecutive_failures"] == 1
    assert snap["state"] == "closed"


def test_delegate_clears_circuit_breaker_on_success(monkeypatch):
    import core.ai.circuit_breaker as cb
    import core.ai_provider as ai_provider

    # Pre-set the breaker to open
    for _ in range(cb.CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("groq")

    assert cb.is_open("groq") is True

    # Manually transition to half-open and have the attempt succeed
    cb._save_state({})

    # Disable deepseek_native_flash so groq is tried first (and succeeds).
    monkeypatch.setitem(ai_provider.get_provider("deepseek_native_flash"), "available_fn", lambda: False)

    groq = ai_provider.get_provider("groq")
    monkeypatch.setitem(groq, "available_fn", lambda: True)
    monkeypatch.setitem(groq, "run_text_task",
                        lambda p, timeout=60, project_path=None: "groq is back")

    # groq is no longer open (cleared above) -- it should succeed and the
    # breaker should stay cleared.
    result = ai_router.delegate("Analyze Docker error log", task_type="log_analysis")

    assert result["provider"] == "groq"
    assert cb.is_open("groq") is False


def test_dashboard_includes_circuit_breaker_and_latency():
    import core.ai.circuit_breaker as cb
    import core.ai.provider_latency as pl

    cb.record_failure("groq")
    pl.record_latency("groq", 200)

    dashboard = ai_router.get_provider_dashboard()

    assert dashboard["groq"]["circuit_breaker"] is not None
    assert dashboard["groq"]["circuit_breaker"]["consecutive_failures"] == 1
    assert dashboard["groq"]["latency"] is not None
    assert dashboard["groq"]["latency"]["last_duration_ms"] == 200
