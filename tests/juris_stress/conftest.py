"""Juris Kai stress-suite fixtures — in-process simulated Telegram users.

Defaults: FAKE AI (instant, deterministic) so the suite is fast and never
burns the GPU fabric. Set JURIS_RUN_REAL_AI=1 to exercise the real router.
Accounts DB is pointed at a temp dir so tests never touch production memory.
"""
import os
import tempfile
import threading
import time
import uuid

import pytest

os.environ.setdefault("JURIS_KAI_DB_DIR", tempfile.mkdtemp(prefix="juris_test_db_"))
os.environ.setdefault("JURIS_KAI_ADMIN_IDS", "999000001")
os.environ.setdefault("JURIS_TEST_FAKE_AI", "1")

FAKE_ANSWER = (
    "Under the Companies Act, 2019 (Act 992), directors owe duties of care "
    "and skill [s. 158-203]. Filing a company's annual returns late attracts "
    "a penalty under s. 226. Sources: Companies Act, 2019 (Act 992)."
)


@pytest.fixture(scope="session")
def bot():
    import core.juris_kai.bot as bot_module
    return bot_module


@pytest.fixture(scope="session")
def fake_ai(bot):
    """Monkeypatch the AI delegate so free-text flows return instantly."""
    calls = []

    def _fake(prompt, task_type, fallback_label, account_id=""):
        calls.append({"prompt": prompt, "task_type": task_type})
        return FAKE_ANSWER, "fake-model"

    original = bot._delegate_with_timeout
    bot._delegate_with_timeout = _fake
    yield calls
    bot._delegate_with_timeout = original


class SimUser:
    """A simulated Telegram user driving the real bot entry point."""

    def __init__(self, bot, name=None, admin=False, user_id=None):
        self.bot = bot
        self.user_id = user_id or str(900_000_000 + uuid.uuid4().int % 10**8)
        self.name = name or f"Sim{self.user_id[-4:]}"
        self.history = []

    def send(self, text):
        reply = self.bot.handle_message({
            "chat_id": self.user_id,
            "text": text,
            "from_first_name": self.name,
        })
        # A real user presses the inline "✅ I Understand" button when gated.
        if reply and "acknowledge the disclaimer" in (reply.get("text") or ""):
            self.bot.handle_callback({
                "id": "cb-test",
                "data": "disclaimer_accept",
                "from": {"id": int(self.user_id)},
                "message": {"chat": {"id": int(self.user_id) or 0}},
            })
            reply = self.bot.handle_message({
                "chat_id": self.user_id,
                "text": text,
                "from_first_name": self.name,
            })
        self.history.append((text, reply))
        return reply

    def text_of(self, reply):
        return (reply or {}).get("text") or ""

    def menu_of(self, reply):
        return (reply or {}).get("reply_markup")


@pytest.fixture
def sim_user(bot, fake_ai):
    return SimUser(bot)


@pytest.fixture
def admin_user(bot, fake_ai):
    u = SimUser(bot, user_id="999000001")
    return u
