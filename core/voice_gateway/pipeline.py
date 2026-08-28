"""VoicePipeline — orchestrates the full wake→VAD→STT→brain→TTS pipeline.

One VoicePipeline instance exists per WSS connection. It owns the audio
lifecycle for a single voice turn from wake word detection through TTS
playback acknowledgement.

Events flow:
  Client sends PCM16 audio frames (binary)
  Gateway runs: wake → VAD → STT → brain → TTS
  Gateway sends: stt.partial, stt.final, brain.start, tts.chunk(+binary), tts.done, turn.end
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from core.ai.voice_realtime import delegate_voice_turn, is_acknowledgement
from core.voice_gateway import events as ev
from core.voice_gateway.elevenlabs_client import synthesize_stream as elab_synth
from core.voice_gateway.events import VoiceEvent, make_event
from core.voice_gateway.piper_client import synthesize_stream as piper_synth, is_acknowledgement as is_piper_ack
from core.voice_gateway.telemetry import VoiceTurnTimer, _now_ms
from core.voice_gateway.vad import create_vad, VoiceActivityDetector
from core.voice_gateway.wake_word import create_detector, WakeWordDetector
from core.voice_gateway.whisper_client import transcribe


class VoicePipeline:
    """Per-connection voice pipeline orchestrator."""

    def __init__(self, ws, session_id: Optional[str] = None):
        self.ws = ws
        self.session_id = session_id or str(uuid.uuid4())[:8]

        # Components — lazily created on first audio
        self._wake_detector: Optional[WakeWordDetector] = None
        self._vad: Optional[VoiceActivityDetector] = None

        # Turn state
        self._turn_timer: Optional[VoiceTurnTimer] = None
        self._interrupted = False
        self._audio_buffer = bytearray()
        self._tts_chunk_index = 0
        self._cancelled = False
        self._last_provider = ""
        self._last_response = ""

        # VAD config
        self._vad_min_silence_ms = 800
        self._vad_hard_cap_ms = 15000

    # -------------------------------------------------------------------------
    # Public API (called by gateway for each WSS message)
    # -------------------------------------------------------------------------

    async def handle_binary(self, audio_bytes: bytes) -> None:
        """Process a PCM16 16kHz mono audio frame from the client."""
        # Run STT in a thread pool — it's CPU-bound
        loop = asyncio.get_event_loop()

        for text, is_final in await loop.run_in_executor(
            None, lambda: list(transcribe(audio_bytes))
        ):
            if text:
                await self.ws.send_str(ev.stt_partial(self.session_id, text).to_json())

        if is_final:
            await self.ws.send_str(ev.stt_final(self.session_id, text).to_json())

    async def handle_wake(self) -> None:
        """Wake word or PTT activated — start a new turn."""
        self._start_turn()
        await self.ws.send_str(ev.make_event("wake.detected", self.session_id).to_json())

    async def handle_vad_stop(self, reason: str = "silence") -> None:
        """VAD detected end of speech — run brain + TTS."""
        await self.ws.send_str(ev.vad_stop(self.session_id, reason).to_json())
        await self._run_brain_and_tts()

    async def handle_tts_done(self) -> None:
        """Client signals it finished playing the last TTS segment."""
        await self._close_turn()

    async def handle_interrupt(self) -> None:
        """'Kai, stop' detected — cancel the current turn."""
        self._cancelled = True
        self._interrupted = True
        await self.ws.send_str(ev.wake_interrupt(self.session_id).to_json())
        await self._close_turn()

    async def handle_ping(self, payload: bytes = b"") -> None:
        """Heartbeat — echo back as pong."""
        await self.ws.send_str(ev.pong(payload))

    # -------------------------------------------------------------------------
    # Internal: turn lifecycle
    # -------------------------------------------------------------------------

    def _start_turn(self) -> None:
        self._turn_timer = VoiceTurnTimer(self.session_id)
        self._turn_timer._t0_stt = _now_ms()  # mark turn start for delta calculation
        self._audio_buffer.clear()
        self._tts_chunk_index = 0
        self._cancelled = False
        self._interrupted = False

    async def _run_brain_and_tts(self) -> None:
        """Run STT → brain routing → TTS streaming for the current turn."""
        if self._cancelled:
            return

        timer = self._turn_timer
        if timer is None:
            return

        # Get the accumulated audio text from STT
        # The pipeline processes audio frames; for now we transcribe accumulated buffer
        audio = bytes(self._audio_buffer) if self._audio_buffer else b""

        # Run transcription (CPU-bound, thread pool)
        loop = asyncio.get_event_loop()
        timer.stt_done()

        try:
            stt_text = await loop.run_in_executor(None, lambda: self._transcribe_buffer())
        except Exception as exc:
            await self.ws.send_str(
                ev.voice_error(self.session_id, "STT_ERROR", str(exc)).to_json()
            )
            await self._close_turn()
            return

        if not stt_text.strip():
            # Nothing to transcribe — silent turn
            await self._close_turn()
            return

        # Brain routing
        await self.ws.send_str(ev.brain_start(self.session_id).to_json())

        # Emit brain.thinking so the HUD can show active generation
        await self.ws.send_str(ev.brain_thinking(self.session_id).to_json())

        try:
            response, provider, brain_ms = await delegate_voice_turn(
                stt_text,
                session_id=self.session_id,
            )
        except RuntimeError as exc:
            await self.ws.send_str(
                ev.voice_error(self.session_id, "BRAIN_ERROR", str(exc)).to_json()
            )
            await self._close_turn()
            return

        timer.brain_done()
        self._last_provider = provider
        self._last_response = response

        # Select TTS engine
        if is_piper_ack(stt_text):
            # Acknowledgement — use Piper (near-instant local CPU)
            await self._stream_piper(response)
        else:
            # Substantive answer — use ElevenLabs
            await self._stream_elevenlabs(response)

        timer.tts_done()
        await self._close_turn()

    async def _stream_piper(self, text: str) -> None:
        """Stream TTS from Piper, sending chunks to client."""
        try:
            chunk_index = 0
            async for chunk in piper_synth(text):
                self._tts_chunk_index += 1
                await self.ws.send_str(
                    ev.tts_chunk(self.session_id, self._tts_chunk_index, is_last=False).to_json()
                )
                await self.ws.send_bytes(chunk)  # Binary audio frame
                chunk_index += 1
            await self.ws.send_str(
                ev.tts_chunk(self.session_id, self._tts_chunk_index, is_last=True).to_json()
            )
            await self.ws.send_str(ev.tts_done(self.session_id).to_json())
        except Exception as exc:
            await self.ws.send_str(
                ev.voice_error(self.session_id, "PIPER_ERROR", str(exc)).to_json()
            )

    async def _stream_elevenlabs(self, text: str) -> None:
        """Stream TTS from ElevenLabs, sending chunks to client."""
        try:
            async for chunk in elab_synth(text):
                self._tts_chunk_index += 1
                await self.ws.send_str(
                    ev.tts_chunk(self.session_id, self._tts_chunk_index, is_last=False).to_json()
                )
                await self.ws.send_bytes(chunk)  # Binary audio frame
            await self.ws.send_str(
                ev.tts_chunk(self.session_id, self._tts_chunk_index, is_last=True).to_json()
            )
            await self.ws.send_str(ev.tts_done(self.session_id).to_json())
        except Exception as exc:
            await self.ws.send_str(
                ev.voice_error(self.session_id, "ELEVENLABS_ERROR", str(exc)).to_json()
            )

    def _transcribe_buffer(self) -> str:
        """Transcribe accumulated audio buffer. Called in thread pool."""
        from core.voice_gateway.whisper_client import quick_transcribe
        audio = bytes(self._audio_buffer)
        if not audio:
            return ""
        return quick_transcribe(audio)

    async def _close_turn(self) -> None:
        """Send turn.end and reset turn state."""
        timer = self._turn_timer
        if timer is None:
            return

        provider = self._last_provider
        text = self._last_response
        try:
            entry = timer.record(text=text, provider=provider)
            timer.write(text=text, provider=provider)
        except Exception:
            pass

        await self.ws.send_str(ev.turn_end(
            self.session_id,
            stt_ms=entry.get("stt_ms", 0),
            brain_ms=entry.get("brain_ms", 0),
            tts_ms=entry.get("tts_ms", 0),
            text=text,
            provider=provider,
        ).to_json())

        self._turn_timer = None
        self._audio_buffer.clear()

    # -------------------------------------------------------------------------
    # VAD / wake word helpers for streaming pipeline
    # -------------------------------------------------------------------------

    async def process_streaming_frame(self, pcm16_bytes: bytes) -> None:
        """Process a single PCM16 frame through wake + VAD + STT in sequence.

        This is the entry point for the streaming pipeline where frames arrive
        continuously from the microphone.
        """
        # Lazy-init detectors on first frame
        if self._wake_detector is None:
            self._wake_detector = create_detector()
        if self._vad is None:
            self._vad = create_vad()

        loop = asyncio.get_event_loop()

        # Wake word check
        for _kw in await loop.run_in_executor(
            None,
            lambda: list(self._wake_detector.process(pcm16_bytes))
        ):
            if _kw == "Hey Kai":
                await self.handle_wake()

        # VAD check
        for vad_event in self._vad.process(pcm16_bytes):
            if vad_event == "speech_end":
                await self.handle_vad_stop()

        # Accumulate audio for STT
        self._audio_buffer.extend(pcm16_bytes)

        # STT partials as audio accumulates (every ~2s of audio)
        if len(self._audio_buffer) >= 32000 * 2:  # 2s at 16kHz
            try:
                text = await loop.run_in_executor(None, self._transcribe_buffer)
                if text.strip():
                    await self.ws.send_str(ev.stt_partial(self.session_id, text).to_json())
            except Exception:
                pass
            # Keep buffer from growing unbounded — keep last 30s
            max_buffer = 16000 * 30
            if len(self._audio_buffer) > max_buffer:
                self._audio_buffer = self._audio_buffer[-max_buffer:]
