import pytest
import sys, os, tempfile, importlib

sys.path.insert(0, "/opt/ai-orchestrator")

from core.kai import infusion


def test_no_card_returns_unmodified(monkeypatch):
    monkeypatch.setattr(infusion, "_read_card", lambda: None)
    out, applied = infusion.infuse("Do the thing", "planning")
    assert out == "Do the thing" and applied is False


def test_card_is_prefixed_for_knowledge_tasks(monkeypatch):
    monkeypatch.setattr(infusion, "_read_card", lambda: "## Kai knowledge card (vtest)\nmodel: qwen3-coder:kai\n")
    out, applied = infusion.infuse("What model do you run on?", "text_task")
    assert applied is True
    assert out.startswith("## Kai knowledge card")
    assert "What model do you run on?" in out


def test_coding_tasks_are_never_infused(monkeypatch):
    monkeypatch.setattr(infusion, "_read_card", lambda: "## Kai knowledge card (vtest)\nX")
    out, applied = infusion.infuse("refactor, code", "coding")
    assert applied is False and out == "refactor, code"


def test_no_double_infusion(monkeypatch):
    monkeypatch.setattr(infusion, "_read_card", lambda: "## Kai knowledge card (vtest)\nX")
    once, _ = infusion.infuse("q", "planning")
    twice, applied2 = infusion.infuse(once, "planning")
    assert applied2 is False
    assert twice.count("## Kai knowledge card") == 1
