"""Browser test isolation.

The browser takeover wiring pages the operator via
``core.notify.human_action``. Tests must never hit the network or write
human-action state into the production memory dir, so every browser test gets
an isolated memory dir and a stubbed Telegram send.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_human_action(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path / "memory"))
    from core.notify import human_action

    sent = []

    def _send(text):
        sent.append(text)
        return {"ok": True, "result": {"message_id": len(sent)}}

    monkeypatch.setattr(human_action, "_send_telegram", _send)
    return sent
