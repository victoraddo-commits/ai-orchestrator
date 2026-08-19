"""Tests for Kai Betting automation workers."""

import os
from unittest.mock import patch


class TestZeroPicksAlert:
    """Verify _check_zero_picks_alert() only fires when games existed but nothing qualified."""

    def test_no_alert_when_no_events_had_odds(self):
        from core.kai_betting.workers import KaiBettingWorkers
        workers = KaiBettingWorkers()
        result = workers._check_zero_picks_alert({"events_with_odds": 0, "qualified": 0})
        assert result == {"alerted": False}

    def test_no_alert_when_predictions_qualified(self):
        from core.kai_betting.workers import KaiBettingWorkers
        workers = KaiBettingWorkers()
        result = workers._check_zero_picks_alert({"events_with_odds": 12, "qualified": 3})
        assert result == {"alerted": False}

    def test_no_admin_chat_id_configured(self):
        from core.kai_betting.workers import KaiBettingWorkers
        workers = KaiBettingWorkers()
        with patch.dict(os.environ, {"BETTING_ADMIN_CHAT_ID": ""}, clear=False):
            result = workers._check_zero_picks_alert({"events_with_odds": 8, "qualified": 0})
        assert result == {"alerted": False, "reason": "no_admin_chat_id"}

    def test_alerts_when_events_existed_but_nothing_qualified(self):
        from core.kai_betting.workers import KaiBettingWorkers
        workers = KaiBettingWorkers()
        with patch.dict(os.environ, {"BETTING_ADMIN_CHAT_ID": "12345"}, clear=False):
            with patch("core.kai_betting.telegram_bot.BettingTelegramBot.send_raw",
                       return_value=True) as mock_send:
                result = workers._check_zero_picks_alert({"events_with_odds": 8, "qualified": 0})
        assert result == {"alerted": True}
        mock_send.assert_called_once()
        chat_id_arg = mock_send.call_args[0][0]
        assert chat_id_arg == "12345"

    def test_alert_reflects_send_failure(self):
        from core.kai_betting.workers import KaiBettingWorkers
        workers = KaiBettingWorkers()
        with patch.dict(os.environ, {"BETTING_ADMIN_CHAT_ID": "12345"}, clear=False):
            with patch("core.kai_betting.telegram_bot.BettingTelegramBot.send_raw",
                       return_value=False):
                result = workers._check_zero_picks_alert({"events_with_odds": 8, "qualified": 0})
        assert result == {"alerted": False}
