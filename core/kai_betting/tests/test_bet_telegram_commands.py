"""Unit tests for KAI Bet Telegram command routing (§30). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.telegram_commands import is_betting_command, BETTING_COMMANDS


class TestBettingCommands(unittest.TestCase):
    def test_recognises_betting_commands(self):
        for c in ("/picks", "/odds", "/performance", "/results", "/subscribe",
                  "/sports", "/myaccount", "/help", "/start", "/day", "/stats", "/predictions"):
            self.assertTrue(is_betting_command(c), c)

    def test_strips_bot_suffix(self):
        self.assertTrue(is_betting_command("/picks@KaiEnzo_bot"))
        self.assertTrue(is_betting_command("/odds@SomeOtherBot extra"))

    def test_ignores_non_betting(self):
        self.assertFalse(is_betting_command("/pending"))
        self.assertFalse(is_betting_command("hello"))
        self.assertFalse(is_betting_command(""))
        self.assertFalse(is_betting_command(None))

    def test_returns_none_for_non_command(self):
        # handler must not raise / must return None for non-betting text
        from core.kai_betting.telegram_commands import handle_betting_command
        self.assertIsNone(handle_betting_command("just chatting"))

    def test_command_list_nonempty(self):
        self.assertGreaterEqual(len(BETTING_COMMANDS), 10)


if __name__ == "__main__":
    unittest.main()
