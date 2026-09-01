# Telegram Voice AI — Phase 17N Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Telegram voice message support — receive voice note → transcribe → Kai reasoning → TTS voice reply — via the existing voice_router infrastructure.

**Architecture:** `poll_updates()` gains voice support, a new `_download_telegram_file()` helper handles audio retrieval, `route_inbound_reply()` dispatches voice inputs through `voice_router.transcribe()` → Kai chat → `voice_router.speak()` → Telegram voice reply. Falls back to text when voice pipeline is unavailable.

**Tech Stack:** Python standard library + `requests` (already in use by telegram_bridge), voice_router (already in core/).

---

## Task 1: Add voice message detection to poll_updates()

**Files:**
- Modify: `core/telegram_bridge.py:271-301`

- [ ] **Step 1: Extend message dict to include voice field**

In `poll_updates()`, the `msg` dict from Telegram's Bot API includes a `voice` key when the message is a voice note. The current code at line 278-281 skips non-text messages:

```python
text = (msg.get("text") or "").strip()
if not text:
    continue
```

Replace the `if not text: continue` block with:

```python
text = (msg.get("text") or "").strip()
voice = msg.get("voice")

if not text and not voice:
    continue

# Build the message dict — same as before, plus voice metadata
msg_dict = {
    "update_id": update_id,
    "chat_id": msg_chat_id,
    "text": text,
    "reply_to_message_id": (msg.get("reply_to_message") or {}).get("message_id"),
    "from": {
        "id": str((msg.get("from") or {}).get("id", "")),
        "username": (msg.get("from") or {}).get("username", ""),
        "first_name": (msg.get("from") or {}).get("first_name", ""),
    },
}

if voice:
    msg_dict["voice"] = {
        "file_id": voice.get("file_id", ""),
        "duration": voice.get("duration", 0),  # seconds
        "mime_type": voice.get("mime_type", "audio/ogg"),
    }

messages.append(msg_dict)
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_telegram_bridge.py -v`
Expected: PASS (existing tests still pass)

---

## Task 2: Add Telegram file download helper

**Files:**
- Modify: `core/telegram_bridge.py`

- [ ] **Step 1: Add _download_file() after the token/URL helpers**

Add this function after `_api_url()` (around line 68):

```python
def _download_file(file_id, token=None):
    """Download a file (voice, audio, photo) from Telegram's file API.

    Returns raw bytes of the file content, or raises RuntimeError.
    Voice messages from Telegram are in OGG (Opus) format.
    """
    if token is None:
        token = _load_token()

    try:
        response = requests.get(
            _api_url(f"getFile", token),
            params={"file_id": file_id},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(
            f"Telegram getFile failed for {file_id}: {type(error).__name__}"
        ) from error

    if not body.get("ok"):
        raise RuntimeError(
            f"Telegram getFile not ok: {body.get('description', 'unknown')}"
        )

    result = body.get("result", {})
    file_path = result.get("file_path")
    if not file_path:
        raise RuntimeError(f"Telegram getFile: no file_path in response for {file_id}")

    # Assemble the full download URL from the Telegram CDN
    file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"

    try:
        audio_resp = requests.get(file_url, timeout=60)
        audio_resp.raise_for_status()
        return audio_resp.content
    except Exception as error:
        raise RuntimeError(
            f"Telegram file download failed for {file_path}: {type(error).__name__}"
        ) from error
```

---

## Task 3: Add voice message routing in route_inbound_reply()

**Files:**
- Modify: `core/telegram_bridge.py:780-804` (the kai_chat fallback path in `route_inbound_reply`)

- [ ] **Step 1: Add _route_voice_message() helper function**

Add this function before `route_inbound_reply()` (around line 725, after `_build_from_reply_to`):

```python
def _route_voice_message(message):
    """Handle a voice message: transcribe → Kai chat → TTS → voice reply.

    Returns a dict with:
      - routed: True
      - action: "voice_reply"
      - reply_text: the Kai-generated text (for logging/debugging)
      - audio_bytes: raw PCM/WAV bytes of the TTS response (for Telegram send_voice)
      - chat_id: destination chat_id
    Or raises RuntimeError on failure.
    """
    voice = message.get("voice", {})
    file_id = voice.get("file_id")
    if not file_id:
        raise RuntimeError("voice message missing file_id")

    # Download the audio
    audio_bytes = _download_file(file_id)

    # Transcribe via voice_router (local-first, cloud fallback)
    from core.voice_router import transcribe
    result = transcribe(audio_bytes, filename="voice.ogg")

    if not result.get("ok"):
        raise RuntimeError(f"STT failed: {result.get('error', 'unknown')}")

    transcribed_text = result.get("text", "").strip()
    if not transcribed_text:
        raise RuntimeError("STT returned empty text")

    # Route to Kai chat (same handler as text messages)
    from_info = message.get("from", {})
    operator = _operator_name(from_info)

    _import_kai_chat()
    try:
        reply = _handle_kai_chat(transcribed_text, operator)
    except Exception as exc:
        raise RuntimeError(f"Kai chat error: {exc}") from exc

    # Extract reply text
    if reply.get("response") is not None:
        reply_text = str(reply["response"])
    elif reply.get("result") is not None:
        result_data = reply["result"]
        reply_text = result_data if isinstance(result_data, str) else json.dumps(result_data, indent=2, default=str)
    elif reply.get("error"):
        reply_text = str(reply["error"])
    else:
        reply_text = str(reply)

    # Synthesize speech via voice_router (local-first, cloud fallback)
    from core.voice_router import speak
    tts_result = speak(reply_text)

    if not tts_result.get("ok"):
        raise RuntimeError(f"TTS failed: {tts_result.get('error', 'unknown')}")

    audio_bytes = tts_result.get("audio")
    if not audio_bytes:
        raise RuntimeError("TTS returned no audio")

    return {
        "routed": True,
        "action": "voice_reply",
        "reply_text": reply_text,
        "audio_bytes": audio_bytes,
        "chat_id": message.get("chat_id"),
    }
```

- [ ] **Step 2: Update route_inbound_reply() to handle voice messages**

In `route_inbound_reply()`, around line 778, the existing code is:

```python
    if not text:
        return {"routed": False, "reply": "Empty message."}

    _import_kai_chat()
    try:
        reply = _handle_kai_chat(text, operator)
```

Replace with:

```python
    voice = message.get("voice")
    if voice:
        # Voice message path — transcribe, reason, synthesize
        result = _route_voice_message(message)
        return {
            "routed": True,
            "action": result["action"],
            "operator": operator,
            "reply_text": result["reply_text"],
            "audio_bytes": result["audio_bytes"],
            "chat_id": result["chat_id"],
        }

    if not text:
        return {"routed": False, "reply": "Empty message."}

    _import_kai_chat()
    try:
        reply = _handle_kai_chat(text, operator)
```

- [ ] **Step 3: Add send_voice() to telegram_bridge**

Add this function after `send_typing()` (around line 129):

```python
def send_voice(audio_bytes, chat_id=None, token=None, duration=None):
    """Send a voice message (WAV audio) to the allowed chat.

    Telegram accepts WAV for voice messages. The voice_router.speak() returns
    raw PCM16 16kHz mono bytes; this function wraps them in a WAV container
    (no external dependencies required).
    """
    if token is None:
        token = _load_token()
    if chat_id is None:
        chat_id = ALLOWED_CHAT_ID

    import io
    import wave as _wave

    # voice_router returns PCM16 16kHz mono — wrap in WAV
    wav_buffer = io.BytesIO()
    with _wave.open(wav_buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(16000)
        wav.writeframes(audio_bytes)
    wav_buffer.seek(0)
    wav_bytes = wav_buffer.read()

    try:
        response = requests.post(
            _api_url("sendVoice", token),
            data={"chat_id": chat_id},
            files={"voice": ("voice.wav", wav_bytes, "audio/wav")},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(
            f"Telegram sendVoice failed: {type(error).__name__}"
        ) from error

    if not body.get("ok"):
        raise RuntimeError(
            f"Telegram sendVoice returned not ok: {body.get('description', 'unknown')}"
        )

    return body
```

- [ ] **Step 4: Update poll_once() in telegram_poller.py to send voice replies**

In `poll_once()`, after `reply_text = result.get("reply")`, the poller checks for text and sends. We need to handle `audio_bytes` too.

In `core/telegram_poller.py`, update the `poll_once()` function around line 83:

Find:
```python
        reply_text = result.get("reply")

        if reply_text:
            _safe_send(reply_text)
```

Replace with:
```python
        reply_text = result.get("reply")
        audio_bytes = result.get("audio_bytes")

        if audio_bytes:
            try:
                from core.telegram_bridge import send_voice
                send_voice(audio_bytes, chat_id=result.get("chat_id"))
            except Exception as error:
                info(f"telegram_poller: voice send failed: {type(error).__name__}")
                # Fall back to text if voice fails
                if reply_text:
                    _safe_send(reply_text)
        elif reply_text:
            _safe_send(reply_text)
```

---

## Task 4: Add tests for voice message handling

**Files:**
- Create: `tests/test_telegram_voice.py`

- [ ] **Step 1: Write tests for poll_updates voice detection**

```python
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
    @patch("core.telegram_bridge._import_kai_chat")
    def test_route_voice_message_success(self, mock_import, mock_download):
        mock_download.return_value = b"fake ogg bytes"

        mock_chat = MagicMock()
        mock_chat.return_value = {"response": "Builds are running fine."}
        mock_import.return_value = None

        # Patch the imported handle_kai_chat
        with patch("core.api.handle_kai_chat", mock_chat):
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
```

- [ ] **Step 2: Run voice tests**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_telegram_voice.py -v`
Expected: PASS (all 4 tests)

---

## Task 5: Run full test suite

- [ ] **Step 1: Run all telegram-related tests**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_telegram_*.py tests/test_telegram_bridge.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run voice router tests**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_voice_router.py -v 2>/dev/null || echo "no test file yet — skipping"`
Expected: PASS or SKIP

---

## Task 6: Update roadmap.json

- [ ] **Step 1: Mark phase 17N as in_progress**

In `roadmap.json`, update phase 17N:

```json
"17N": {
  "name": "Voice/Phone AI (MiniMax)",
  "status": "in_progress",
  "completion_note": "Option C: Telegram voice messages — transcribe via voice_router, respond via TTS, reply as voice note"
}
```

- [ ] **Step 2: Commit**

```bash
git add core/telegram_bridge.py core/telegram_poller.py tests/test_telegram_voice.py roadmap.json
git commit -m "feat(17N): Telegram voice AI — receive voice note, transcribe, Kai reason, TTS reply"
```
