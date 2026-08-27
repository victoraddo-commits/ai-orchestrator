#!/usr/bin/env python3
"""Kai Voice Gateway — CLI test client for Phase 1 acceptance testing.

Usage:
    python -m core.voice_gateway.test_client [--mic] [--text "Hello Kai"]

    --mic     : Stream real microphone audio to the gateway
    --text    : Send a text query (synthesizes audio, then processes)
    --gateway : Override gateway URL (default: ws://127.0.0.1:8130/ws)

Acceptance criterion:
    Wake-to-first-audio < 1.2s measured

The test client opens a WSS connection, optionally streams microphone audio,
and prints all events received from the gateway including latency breakdowns.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
import sys
import time
from typing import Optional

import aiohttp
import numpy as np


DEFAULT_GATEWAY = "ws://localhost:8000/kai-voice/ws"
SAMPLE_RATE = 16000
CHUNK_MS = 100        # 100ms frames
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # PCM16 = sample_rate * 2 bytes/s


class VoiceTestClient:
    """CLI test client for the Kai Voice Gateway."""

    def __init__(self, gateway_url: str = DEFAULT_GATEWAY):
        self.gateway_url = gateway_url
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self.t0_wake: Optional[float] = None
        self.t0_first_audio: Optional[float] = None

    # -------------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------------

    async def connect(self) -> None:
        async with aiohttp.ClientSession() as session:
            self.ws = await session.ws_connect(
                self.gateway_url,
                autoclose=True,
                autoping=True,
            )
            print(f"[CONNECTED] {self.gateway_url}")
            self.running = True

            async def send_audio_loop():
                """Read mic and stream PCM16 frames to the gateway."""
                try:
                    import pyaudio
                    p = pyaudio.PyAudio()
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=SAMPLE_RATE,
                        input=True,
                        frames_per_buffer=CHUNK_BYTES // 2,
                    )
                    print("[MIC] Recording... Press Ctrl+C to stop.")

                    while self.running:
                        data = stream.read(CHUNK_BYTES // 2, exception_on_overflow=False)
                        if self.ws and self.ws.is_connected():
                            await self.ws.send_bytes(data)
                except ImportError:
                    print("[MIC] pyaudio not installed — use --text mode instead")

            async def receive_loop():
                """Print all events from the gateway."""
                while self.running:
                    if self.ws is None:
                        break
                    try:
                        msg = await self.ws.receive()
                    except Exception:
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        raw = msg.data
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        self._handle_event(evt)

                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        # First TTS audio chunk — record first-audio time
                        if self.t0_first_audio is None and self.t0_wake is not None:
                            self.t0_first_audio = time.monotonic()
                            elapsed = (self.t0_first_audio - self.t0_wake) * 1000
                            print(f"\n[FIRST AUDIO] {elapsed:.0f}ms after wake")
                            if elapsed < 1200:
                                print("[PASS] wake-to-first-audio < 1.2s")
                            else:
                                print(f"[FAIL] {elapsed:.0f}ms exceeds 1.2s threshold")

                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        print("[DISCONNECTED]")
                        self.running = False
                        break

            # Send a wake.detected to start the turn
            await self.ws.send_str(json.dumps({
                "type": "wake.detected",
                "session_id": "test-client",
                "data": {},
            }))
            self.t0_wake = time.monotonic()
            print(f"[WATCH] Turn started at {self.t0_wake:.3f}")

            await asyncio.gather(send_audio_loop(), receive_loop())

    def _handle_event(self, evt: dict) -> None:
        """Print events with timing info."""
        t = evt.get("type", "")
        ts = evt.get("timestamp", "")
        sid = evt.get("session_id", "")
        data = evt.get("data", {})

        if t == "stt.partial":
            print(f"  [STT partial] {data.get('text', '')}")
        elif t == "stt.final":
            print(f"  [STT final] {data.get('text', '')}")
        elif t == "brain.start":
            print(f"  [BRAIN] started → {data.get('provider', '')}")
        elif t == "tts.chunk":
            idx = data.get("index", 0)
            is_last = data.get("is_last", False)
            marker = " (LAST)" if is_last else ""
            print(f"  [TTS chunk #{idx}]{marker}")
        elif t == "tts.done":
            print("  [TTS done]")
        elif t == "turn.end":
            stt = data.get("stt_ms", 0)
            brain = data.get("brain_ms", 0)
            tts = data.get("tts_ms", 0)
            total = data.get("total_ms", 0)
            print(f"  [TURN END] STT={stt}ms brain={brain}ms TTS={tts}ms total={total}ms")
            print(f"  [TURN END] provider={data.get('provider', '')} text={data.get('text', '')!r:.60}")
        elif t == "error":
            print(f"  [ERROR] {data.get('code', '')}: {data.get('message', '')}")
        elif t == "wake.interrupt":
            print("  [INTERRUPT] wake interrupted")
        elif t == "vad.stop":
            print(f"  [VAD] stopped: {data.get('reason', '')}")
        elif t == "pong":
            print("  [PONG]")
        elif t == "wake.detected":
            print("  [WAKE] detected")


async def run_text_test(client: VoiceTestClient, text: str) -> None:
    """Send a text query through the gateway (uses Piper for acknowledgement)."""
    print(f"[TEXT MODE] Sending: {text!r}")
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(client.gateway_url, autoclose=True)
        t0 = time.monotonic()
        await ws.send_str(json.dumps({
            "type": "wake.detected",
            "session_id": "text-test",
            "data": {"text": text},
        }))

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                raw = msg.data
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = evt.get("type", "")
                data = evt.get("data", {})
                if t == "turn.end":
                    elapsed = (time.monotonic() - t0) * 1000
                    print(f"  [TURN END] total={elapsed:.0f}ms")
                    print(f"  [TURN END] provider={data.get('provider', '')}")
                    break
                elif t == "error":
                    print(f"  [ERROR] {data.get('code', '')}: {data.get('message', '')}")
            elif msg.type == aiohttp.WSMsgType.BINARY:
                elapsed = (time.monotonic() - t0) * 1000
                if client.t0_first_audio is None:
                    client.t0_first_audio = time.monotonic()
                    print(f"\n🎙 FIRST AUDIO: {elapsed:.0f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kai Voice Gateway CLI test client")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY,
                        help="WSS gateway URL")
    parser.add_argument("--text", default=None,
                        help="Send a text query (bypasses mic)")
    parser.add_argument("--mic", action="store_true",
                        help="Stream microphone audio")
    args = parser.parse_args()

    client = VoiceTestClient(args.gateway)

    if args.text:
        asyncio.run(run_text_test(client, args.text))
    elif args.mic:
        try:
            asyncio.run(client.connect())
        except KeyboardInterrupt:
            print("\n[STOPPED]")
    else:
        print("Specify --text 'query' or --mic")
        print("Run gateway: python -m core.voice_gateway.gateway")


if __name__ == "__main__":
    main()
