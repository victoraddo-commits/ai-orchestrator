"""Regression: token_for must resolve tokens from a bot's dotenv ``env_file``.

Root cause (2026-09-25): the systemd unit does not inject
``/opt/ai-orchestrator/.env`` into the process environment, and ``token_for``
only read ``os.environ`` + raw token files — so every bot token resolved to
MISSING at runtime (CLI, scheduler, ad-hoc callers).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.telegram import transport as t


class _Bot:
    def __init__(self, token_env="", env_file=""):
        self.token_env = token_env
        self.env_file = env_file


def test_token_from_dotenv_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nKAI_TELEGRAM_BOT_TOKEN=12345:ABCdef\nOTHER=x\n")
    monkeypatch.delenv("KAI_TELEGRAM_BOT_TOKEN", raising=False)
    assert t.token_for(_Bot("KAI_TELEGRAM_BOT_TOKEN", str(env))) == "12345:ABCdef"


def test_dotenv_quotes_and_export(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('export KAI_TELEGRAM_BOT_TOKEN="12345:Quoted"\n')
    monkeypatch.delenv("KAI_TELEGRAM_BOT_TOKEN", raising=False)
    assert t.token_for(_Bot("KAI_TELEGRAM_BOT_TOKEN", str(env))) == "12345:Quoted"


def test_env_takes_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("KAI_TELEGRAM_BOT_TOKEN=fromfile\n")
    monkeypatch.setenv("KAI_TELEGRAM_BOT_TOKEN", "fromenv")
    assert t.token_for(_Bot("KAI_TELEGRAM_BOT_TOKEN", str(env))) == "fromenv"


def test_raw_token_file_still_works(tmp_path, monkeypatch):
    raw = tmp_path / "deerude_bot_token"
    raw.write_text("8783853233:RawSecret\n")
    monkeypatch.delenv("DEERUDE_BOT_TOKEN", raising=False)
    assert t.token_for(_Bot("DEERUDE_BOT_TOKEN", str(raw))) == "8783853233:RawSecret"


def test_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("KAI_TELEGRAM_BOT_TOKEN", raising=False)
    assert t.token_for(_Bot("KAI_TELEGRAM_BOT_TOKEN", str(tmp_path / "none"))) == ""
