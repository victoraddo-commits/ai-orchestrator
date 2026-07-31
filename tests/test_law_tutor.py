"""Law Tutor bot tests. Two concerns: (1) the security boundary -- this bot
must never be able to reach any operational capability (approve/deploy/
build), enforced both structurally (no forbidden imports anywhere in the
module's source) and at runtime (no forbidden modules end up in
sys.modules); (2) command dispatch and authorization behave correctly.
"""

import ast
import os
import sys
from pathlib import Path

import pytest

import core.law_tutor.bot as bot
import core.law_tutor.commands as commands
from core.ai.ai_router import AllProvidersFailed


LAW_TUTOR_DIR = Path(__file__).resolve().parent.parent / "core" / "law_tutor"

FORBIDDEN_MODULES = {"core.build_manager", "core.approval", "core.deployment_manager"}


def _imported_modules(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_no_forbidden_operational_imports_anywhere_in_the_source():
    # Parses actual import statements (not a text substring match) so this
    # doesn't false-positive on comments/docstrings that explain the
    # boundary by naming the forbidden modules.
    for path in LAW_TUTOR_DIR.glob("*.py"):
        imported = _imported_modules(path)
        overlap = imported & FORBIDDEN_MODULES
        assert not overlap, f"{path.name} imports forbidden operational module(s): {overlap}"


def test_no_forbidden_operational_modules_get_imported_at_runtime():
    # Must run in a fresh interpreter: within the shared pytest process,
    # unrelated test files legitimately import core.build_manager/
    # core.approval for their own purposes, which would make a same-process
    # sys.modules check false-positive on module leakage that has nothing to
    # do with core.law_tutor.bot itself.
    import subprocess

    script = (
        "import sys, core.law_tutor.bot; "
        "forbidden = {'core.build_manager', 'core.approval', 'core.deployment_manager'}; "
        "leaked = forbidden & set(sys.modules); "
        "print(','.join(sorted(leaked)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    leaked = result.stdout.strip()
    assert not leaked, f"operational modules leaked into sys.modules: {leaked}"


@pytest.fixture
def authorized(monkeypatch):
    monkeypatch.setenv("LAW_TUTOR_CHAT_ID", "555")
    return "555"


def _msg(chat_id, text):
    return {"chat_id": chat_id, "text": text, "from": {"id": "1", "username": "wife", "first_name": "A"}}


def _capturing_delegate(captured):
    def fn(prompt, task_type=None, capability=None):
        captured["task_type"] = task_type
        captured["prompt"] = prompt
        return {"response": "..."}
    return fn


def test_unauthorized_chat_gets_declined_without_touching_ai(monkeypatch, authorized):
    def fail_if_called(*a, **kw):
        raise AssertionError("must not call the AI router for an unauthorized chat")
    monkeypatch.setattr(commands, "delegate", fail_if_called)

    reply = bot.handle_message(_msg("999", "hello"))
    assert "private" in reply.lower()


def test_no_chat_id_configured_declines_everyone(monkeypatch):
    monkeypatch.delenv("LAW_TUTOR_CHAT_ID", raising=False)
    reply = bot.handle_message(_msg("555", "/help"))
    assert "private" in reply.lower()


def test_help_and_start_return_help_text(authorized):
    assert bot.handle_message(_msg(authorized, "/help")) == bot.HELP_TEXT
    assert bot.handle_message(_msg(authorized, "/start")) == bot.HELP_TEXT


def test_unknown_command_gives_a_helpful_message(authorized):
    reply = bot.handle_message(_msg(authorized, "/notacommand"))
    assert "/help" in reply


def test_learn_without_a_topic_returns_usage_and_does_not_call_ai(monkeypatch, authorized):
    def fail_if_called(*a, **kw):
        raise AssertionError("must not call the AI router with no topic")
    monkeypatch.setattr(commands, "delegate", fail_if_called)

    reply = bot.handle_message(_msg(authorized, "/learn"))
    assert "Usage" in reply


def test_learn_routes_through_law_teaching_task_type(monkeypatch, authorized):
    captured = {}

    def spy_delegate(prompt, task_type=None, capability=None):
        captured["task_type"] = task_type
        captured["prompt"] = prompt
        return {"response": "Negligence requires a duty of care..."}

    monkeypatch.setattr(commands, "delegate", spy_delegate)

    reply = bot.handle_message(_msg(authorized, "/learn negligence"))

    assert captured["task_type"] == "law_teaching"
    assert "negligence" in captured["prompt"].lower()
    assert reply == "Negligence requires a duty of care..."


def test_case_routes_through_law_case_analysis(monkeypatch, authorized):
    captured = {}
    monkeypatch.setattr(commands, "delegate", _capturing_delegate(captured))

    bot.handle_message(_msg(authorized, "/case Donoghue v Stevenson"))

    assert captured["task_type"] == "law_case_analysis"


def test_research_routes_through_law_case_analysis(monkeypatch, authorized):
    captured = {}
    monkeypatch.setattr(commands, "delegate", _capturing_delegate(captured))

    bot.handle_message(_msg(authorized, "/research evolution of duty of care"))

    assert captured["task_type"] == "law_case_analysis"


def test_argument_routes_through_law_case_analysis(monkeypatch, authorized):
    captured = {}
    monkeypatch.setattr(commands, "delegate", _capturing_delegate(captured))

    bot.handle_message(_msg(authorized, "/argument should strict liability apply"))

    assert captured["task_type"] == "law_case_analysis"


def test_research_and_argument_without_args_return_usage(authorized):
    assert "Usage" in bot.handle_message(_msg(authorized, "/research"))
    assert "Usage" in bot.handle_message(_msg(authorized, "/argument"))


def test_flashcards_routes_through_law_flashcards(monkeypatch, authorized):
    captured = {}
    monkeypatch.setattr(commands, "delegate", _capturing_delegate(captured))

    bot.handle_message(_msg(authorized, "/flashcards offer and acceptance"))

    assert captured["task_type"] == "law_flashcards"


def test_plain_message_without_a_slash_falls_back_to_learn(monkeypatch, authorized):
    captured = {}
    monkeypatch.setattr(commands, "delegate", _capturing_delegate(captured))

    bot.handle_message(_msg(authorized, "what is consideration in contract law?"))

    assert captured["task_type"] == "law_teaching"


def test_progress_does_not_call_the_ai_router(monkeypatch, authorized):
    def fail_if_called(*a, **kw):
        raise AssertionError("/progress must be a local read, not an AI call")
    monkeypatch.setattr(commands, "delegate", fail_if_called)

    reply = bot.handle_message(_msg(authorized, "/progress"))
    assert "Topics studied" in reply


def test_all_providers_failed_gives_a_graceful_reply_not_a_crash(monkeypatch, authorized):
    def boom(*a, **kw):
        raise AllProvidersFailed("no provider available")
    monkeypatch.setattr(commands, "delegate", boom)

    reply = bot.handle_message(_msg(authorized, "/learn negligence"))
    assert "unavailable" in reply.lower()
