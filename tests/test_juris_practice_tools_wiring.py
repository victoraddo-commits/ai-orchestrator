"""TDD: practice/research tools wiring (Legal Brain 2.0 Phase 6, Task 4).

Covers the menu buttons, the slash commands and the bot conversation steps that
expose :mod:`core.juris_kai.tools`. Tool internals are monkeypatched so no
network/generation is performed here.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Pin the account DB to an isolated test dir *before* importing commands/bot
# (core.juris_kai.accounts computes its DB path at import time). This keeps the
# module from initialising the real account DB and polluting sibling suites.
os.environ.setdefault("JURIS_KAI_DB_DIR",
                      str(Path(tempfile.gettempdir()) / "juris_kai_test"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.juris_kai import commands, menus  # noqa: E402


def _labels(keyboard_json):
    kb = json.loads(keyboard_json)
    return {b["text"] for row in kb["keyboard"] for b in row}


def _account():
    return {"account_id": "acct-1"}


def _update():
    return {"chat_id": 42}


# --- menus ------------------------------------------------------------------

def test_practice_menu_has_the_four_tools():
    labels = _labels(menus.practice_menu())
    for label in ("📄 Contract Analysis", "🧮 Issue Matrix",
                  "🗓️ Legal Chronology", "📚 Authority Bundle"):
        assert label in labels, label


def test_learn_menu_has_research_modes():
    labels = _labels(menus.learn_menu())
    assert "📜 Statute Search" in labels
    assert "⚖️ Case-law" in labels


def test_menu_for_text_routes_practice_and_learn():
    assert "Contract Analysis" in menus.menu_for_text("📝 Practice")
    assert "Statute Search" in menus.menu_for_text("📚 Learn Law")


# --- slash commands ---------------------------------------------------------

def test_command_statute_renders(monkeypatch):
    from core.juris_kai import tools
    monkeypatch.setattr(tools, "research",
                        lambda q, mode="quick", **k: {"rendered": f"S:{q}:{mode}"})
    assert commands.handle_command("/statute contract law", _update(),
                                   _account()) == "S:contract law:statute"


def test_command_caselaw_renders(monkeypatch):
    from core.juris_kai import tools
    monkeypatch.setattr(tools, "research",
                        lambda q, mode="quick", **k: {"rendered": f"C:{mode}"})
    assert commands.handle_command("/caselaw foo", _update(),
                                   _account()) == "C:case_law"


def test_command_authorities_renders(monkeypatch):
    from core.juris_kai import tools
    monkeypatch.setattr(tools, "authority_bundle",
                        lambda issues, **k: {"rendered": f"A:{issues[0]}"})
    assert commands.handle_command("/authorities theft", _update(),
                                   _account()) == "A:theft"


def test_command_matrix_renders(monkeypatch):
    from core.juris_kai import tools
    monkeypatch.setattr(tools, "issue_matrix",
                        lambda q, **k: {"rendered": f"M:{q}"})
    assert commands.handle_command("/matrix bail", _update(),
                                   _account()) == "M:bail"


def test_command_chronology_renders(monkeypatch):
    from core.juris_kai import tools
    monkeypatch.setattr(tools, "legal_chronology",
                        lambda f, **k: {"rendered": f"T:{f}"})
    assert commands.handle_command("/chronology 2020 facts", _update(),
                                   _account()) == "T:2020 facts"


def test_command_contract_renders(monkeypatch):
    from core.juris_kai import tools
    monkeypatch.setattr(tools, "contract_analysis",
                        lambda text, **k: {"rendered": f"K:{text}"})
    assert commands.handle_command("/contract shall pay", _update(),
                                   _account()) == "K:shall pay"


def test_tool_commands_show_usage_without_args():
    for cmd in ("/statute", "/caselaw", "/authorities", "/matrix",
                "/chronology", "/contract"):
        assert "Usage" in commands.handle_command(cmd, _update(), _account()), cmd


# --- bot conversation steps -------------------------------------------------

def test_bot_tool_step_renders_and_returns_menu(monkeypatch):
    from core.juris_kai import bot, tools
    monkeypatch.setattr(tools, "legal_chronology",
                        lambda text, **k: {"rendered": "TIMELINE"})
    out = bot._handle_tool_step("chronology", "facts", 1, {})
    assert out["text"] == "TIMELINE"
    assert "Contract Analysis" in out["reply_markup"]


def test_bot_case_law_step_uses_case_menu(monkeypatch):
    from core.juris_kai import bot, tools
    monkeypatch.setattr(tools, "research",
                        lambda text, mode="quick", **k: {"rendered": "NO CASES"})
    out = bot._handle_tool_step("case_law_search", "foo", 1, {})
    assert out["text"] == "NO CASES"
    assert "Case Summaries" in out["reply_markup"]


def test_bot_menu_action_registers_tool_step():
    from core.juris_kai import bot
    bot._conversation_state.clear()
    out = bot._handle_menu_action("📄 Contract Analysis", 99, {}, False, {})
    assert out and "Contract Analysis" in out["text"]
    assert bot._conversation_state["99"]["step"] == "contract"
    bot._conversation_state.clear()
