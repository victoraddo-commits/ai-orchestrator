"""Tests for Telegram voice message handling."""

import pytest
from unittest.mock import patch, MagicMock


class TestPollUpdatesVoiceDetection:
    """poll_updates should detect and return voice messages."""

    def _make_voice_update(self, file_id="voice_file_123", duration=5):
        return {
            "update_id": 999,
            "message": {
                "message_id": 1,
                "chat": {"id": 612786480, "type": "private"},
                "voice": {
                    "file_id": file_id,
                    "duration": duration,
                    "mime_type": "audio/ogg",
                },
                "from": {"id": 612786480, "is_bot": False, "first_name": "Test"},
            },
        }

    @patch("core.telegram_bridge._load_token")
    @patch("core.telegram_bridge.requests.get")
    def test_poll_updates_returns_voice_message(self, mock_get, mock_token):
        mock_token.return_value = "test_token"
        mock_get.return_value = MagicMock(
            ok=True,
            raise_for_status=MagicMock,
            json=lambda: {
                "ok": True,
                "result": [self._make_voice_update()],
            },
        )

        from core.telegram_bridge import poll_updates
        messages = poll_updates()

        assert len(messages) == 1
        assert messages[0]["voice"]["file_id"] == "voice_file_123"
        assert messages[0]["voice"]["duration"] == 5
        assert messages[0]["text"] == ""

    @patch("core.telegram_bridge._load_token")
    @patch("core.telegram_bridge.requests.get")
    def test_poll_updates_skips_text_plus_voice(self, mock_get, mock_token):
        """A message with both text and voice should NOT be skipped."""
        mock_token.return_value = "test_token"
        mock_get.return_value = MagicMock(
            ok=True,
            raise_for_status=MagicMock,
            json=lambda: {
                "ok": True,
                "result": [
                    {
                        "update_id": 999,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 612786480, "type": "private"},
                            "text": "hello",
                            "voice": {
                                "file_id": "voice_file_123",
                                "duration": 5,
                                "mime_type": "audio/ogg",
                            },
                            "from": {
                                "id": 612786480,
                                "is_bot": False,
                                "first_name": "Test",
                            },
                        },
                    }
                ],
            },
        )

        from core.telegram_bridge import poll_updates
        messages = poll_updates()

        assert len(messages) == 1
        assert messages[0]["text"] == "hello"
        assert messages[0]["voice"]["file_id"] == "voice_file_123"


class TestDownloadFile:
    """_download_file should retrieve Telegram file bytes."""

    @patch("core.telegram_bridge._load_token")
    @patch("core.telegram_bridge.requests.get")
    def test_download_file_returns_bytes(self, mock_get, mock_token):
        mock_token.return_value = "test_token"

        # Mock getFile response
        mock_get.side_effect = [
            MagicMock(
                ok=True,
                raise_for_status=MagicMock,
                json=lambda: {
                    "ok": True,
                    "result": {"file_path": "voice/file_123.ogg"},
                },
            ),
            MagicMock(
                status_code=200,
                raise_for_status=MagicMock,
                content=b"fake ogg audio bytes",
            ),
        ]

        from core.telegram_bridge import _download_file
        result = _download_file("voice_file_123")

        assert result == b"fake ogg audio bytes"


class TestRouteVoiceMessage:
    """_route_voice_message should transcribe, reason, and synthesize."""

    @patch("core.telegram_bridge._download_file")
    def test_route_voice_message_success(self, mock_download):
        mock_download.return_value = b"fake ogg bytes"

        mock_chat = MagicMock()
        mock_chat.return_value = {"response": "Builds are running fine."}

        # Let the real _import_kai_chat() run so the global gets set, then
        # patch the global directly so the real function isn't None afterward.
        import core.telegram_bridge as tb
        original_import = tb._import_kai_chat
        original_import()  # populate _handle_kai_chat

        with patch.object(tb, "_handle_kai_chat", mock_chat):
            with patch("core.voice_router.transcribe") as mock_transcribe:
                with patch("core.voice_router.speak") as mock_speak:
                    mock_transcribe.return_value = {
                        "ok": True,
                        "text": "how many builds are running",
                    }
                    mock_speak.return_value = {
                        "ok": True,
                        "audio": b"fake pcm audio bytes",
                    }

                    from core.telegram_bridge import _route_voice_message

                    message = {
                        "chat_id": "612786480",
                        "voice": {"file_id": "abc123", "duration": 5},
                        "from": {"id": "612786480", "username": "test"},
                    }
                    result = _route_voice_message(message)

                    assert result["routed"] is True
                    assert result["action"] == "voice_reply"
                    assert result["reply_text"] == "Builds are running fine."
                    assert result["audio_bytes"] == b"fake pcm audio bytes"

    @patch("core.telegram_bridge._download_file")
    def test_route_voice_message_stt_failure(self, mock_download):
        mock_download.return_value = b"fake ogg bytes"

        with patch("core.voice_router.transcribe") as mock_transcribe:
            mock_transcribe.return_value = {
                "ok": False,
                "error": "gateway down",
            }

            from core.telegram_bridge import _route_voice_message

            message = {
                "chat_id": "612786480",
                "voice": {"file_id": "abc123", "duration": 5},
                "from": {"id": "612786480"},
            }

            with pytest.raises(RuntimeError, match="STT failed"):
                _route_voice_message(message)


class TestDownloadFileErrors:
    """_download_file error paths."""

    @patch("core.telegram_bridge._load_token")
    @patch("core.telegram_bridge.requests.get")
    def test_download_file_getfile_returns_not_ok(self, mock_get, mock_token):
        mock_token.return_value = "test_token"
        mock_get.return_value = MagicMock(
            ok=True,
            raise_for_status=MagicMock,
            json=lambda: {"ok": False, "description": "File not found"},
        )
        from core.telegram_bridge import _download_file
        with pytest.raises(RuntimeError, match="not ok"):
            _download_file("bad_file_id")

    @patch("core.telegram_bridge._load_token")
    @patch("core.telegram_bridge.requests.get")
    def test_download_file_cdn_fetch_fails(self, mock_get, mock_token):
        mock_token.return_value = "test_token"
        mock_get.side_effect = [
            MagicMock(
                ok=True, raise_for_status=MagicMock,
                json=lambda: {"ok": True, "result": {"file_path": "voice/f.ogg"}},
            ),
            MagicMock(
                status_code=500,
                raise_for_status=MagicMock(side_effect=Exception("cdn error")),
            ),
        ]
        from core.telegram_bridge import _download_file
        with pytest.raises(RuntimeError, match="download failed|Telegram file download failed"):
            _download_file("file_id")


class TestRouteVoiceMessageErrors:
    """_route_voice_message error paths."""

    @patch("core.telegram_bridge._download_file")
    def test_route_voice_message_empty_transcript(self, mock_download):
        """STT returns ok=True but text is empty string."""
        mock_download.return_value = b"ogg"
        with patch("core.voice_router.transcribe") as mock_transcribe:
            mock_transcribe.return_value = {"ok": True, "text": "   "}
            from core.telegram_bridge import _route_voice_message
            message = {"chat_id": "1", "voice": {"file_id": "x"}, "from": {"id": "1"}}
            with pytest.raises(RuntimeError, match="empty text"):
                _route_voice_message(message)

    @patch("core.telegram_bridge._download_file")
    @patch("core.telegram_bridge._import_kai_chat")
    def test_route_voice_message_tts_failure(self, mock_import, mock_download):
        """speak returns ok=False."""
        mock_download.return_value = b"ogg"
        mock_import.return_value = None
        with patch("core.api.handle_kai_chat") as mock_chat:
            mock_chat.return_value = {"response": "hello"}
            with patch("core.voice_router.transcribe") as mock_transcribe:
                mock_transcribe.return_value = {"ok": True, "text": "hi"}
                with patch("core.voice_router.speak") as mock_speak:
                    mock_speak.return_value = {"ok": False, "error": "no audio"}
                    from core.telegram_bridge import _route_voice_message
                    message = {"chat_id": "1", "voice": {"file_id": "x"}, "from": {"id": "1"}}
                    with pytest.raises(RuntimeError, match="TTS failed"):
                        _route_voice_message(message)

    def test_route_voice_message_missing_file_id(self):
        """Voice dict missing file_id raises RuntimeError."""
        from core.telegram_bridge import _route_voice_message
        message = {"chat_id": "1", "voice": {}, "from": {"id": "1"}}
        with pytest.raises(RuntimeError, match="file_id"):
            _route_voice_message(message)
