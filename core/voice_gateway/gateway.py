"""Kai Voice Gateway — FastAPI-native WSS endpoint.

Mounted on the orchestrator's FastAPI app alongside the HTTP routes.
The standalone runner (python -m core.voice_gateway.gateway) starts the
aiohttp server directly when running as a systemd service.

Binary protocol:
  - Client → Gateway: raw PCM16 16kHz mono audio frames (binary WebSocket frames)
  - Gateway → Client: JSON events + binary TTS audio frames

JSON events follow the 10-event vocabulary in events.py.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import ssl
import time
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from core.voice_gateway import events as ev
from core.voice_gateway.pipeline import VoicePipeline


# Configuration
BIND_HOST = os.environ.get("KAI_VOICE_BIND", "0.0.0.0")
BIND_PORT = int(os.environ.get("KAI_VOICE_PORT", "8130"))
HEARTBEAT_INTERVAL = 15
IDLE_TIMEOUT = 30
MAX_BINARY_FRAME = 1024 * 64


# ---------------------------------------------------------------------------
# FastAPI WebSocket router
# ---------------------------------------------------------------------------

voice_router = APIRouter(tags=["voice"])


@voice_router.websocket("/ws")
async def wss_endpoint(ws: WebSocket):
    """FastAPI-native WebSocket endpoint for the voice pipeline."""
    await ws.accept()
    session_id = _make_session_id()
    pipeline = VoicePipeline(ws, session_id=session_id)

    _log("info", f"WSS connection opened: session={session_id}")

    try:
        while True:
            if ws.state != WebSocketState.CONNECTED:
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=60)
            except asyncio.TimeoutError:
                continue

            # FastAPI WebSocket message types: 'text', 'binary', 'ping', 'pong', 'close'
            if msg.type == "binary":
                audio_bytes = msg.data
                if len(audio_bytes) <= MAX_BINARY_FRAME:
                    await pipeline.process_streaming_frame(audio_bytes)

            elif msg.type == "text":
                raw = msg.data
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = obj.get("type", "")
                event_data = obj.get("data", {})

                if event_type == "wake.detected":
                    await pipeline.handle_wake()
                elif event_type == "vad.stop":
                    reason = event_data.get("reason", "silence")
                    await pipeline.handle_vad_stop(reason)
                elif event_type == "tts.done":
                    await pipeline.handle_tts_done()
                elif event_type == "wake.interrupt":
                    await pipeline.handle_interrupt()
                elif event_type in ("ping", "ping_frame"):
                    await pipeline.handle_ping(b"")

            elif msg.type in ("ping", "pong"):
                pass  # FastAPI auto-handles these

            elif msg.type == "close":
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        _log("error", f"WSS handler error {session_id}: {exc}")
    finally:
        _log("info", f"WSS connection closed: session={session_id}")


# ---------------------------------------------------------------------------
# Health endpoint (also serves as FastAPI GET for /health)
# ---------------------------------------------------------------------------

@voice_router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "kai-voice",
        "version": "1.0.0",
        "timestamp": _iso_now(),
    }


# ---------------------------------------------------------------------------
# Standalone aiohttp runner (used by systemd service)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# HTTP TTS/STT endpoints for the standalone runner.
# voice_router (used by /kai/voice/speak|transcribe) expects these on :8130.
# They return real audio/transcripts or a typed error — never fabricated output.
# ---------------------------------------------------------------------------

def _wav_to_pcm16(data: bytes) -> bytes:
    """If data is a RIFF/WAVE container, return its PCM frames; else pass through."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        import io as _io
        import wave
        try:
            with wave.open(_io.BytesIO(data), "rb") as w:
                return w.readframes(w.getnframes())
        except Exception:  # noqa: BLE001
            return data
    return data


def _wav_bytes(pcm16: bytes, sample_rate: int = 22050) -> bytes:
    import struct, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


async def _aio_speak(request: web.Request) -> web.Response:
    """GET/POST /speak?text=... -> WAV audio via piper (real synthesis)."""
    text = request.query.get("text", "")
    if not text and request.can_read_body:
        try:
            body = await request.json()
            text = (body or {}).get("text", "")
        except Exception:  # noqa: BLE001
            pass
    if not text:
        return web.json_response({"ok": False, "error": "text required"}, status=400)
    try:
        from core.voice_gateway.piper_client import synthesize_stream
        pcm = bytearray()
        async for chunk in synthesize_stream(text):
            pcm.extend(chunk)
        if not pcm:
            return web.json_response({"ok": False, "error": "no audio produced"}, status=503)
        return web.Response(body=_wav_bytes(bytes(pcm)), content_type="audio/wav")
    except FileNotFoundError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=503)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=503)


async def _aio_transcribe(request: web.Request) -> web.Response:
    """POST /transcribe (raw PCM16 or multipart) -> text via whisper (real STT)."""
    raw = b""
    try:
        if (request.content_type or "").startswith("multipart/"):
            reader = await request.multipart()
            part = await reader.next()
            while part is not None:
                if part.name in ("file", "audio", "upload") or part.filename:
                    raw = await part.read(decode=False)
                    break
                part = await reader.next()
        else:
            raw = await request.read()
    except Exception:  # noqa: BLE001
        raw = b""
    if not raw:
        return web.json_response({"ok": False, "error": "audio required"}, status=400)
    raw = _wav_to_pcm16(raw)
    try:
        from core.voice_gateway.whisper_client import quick_transcribe
        text = quick_transcribe(raw)
        return web.json_response({"ok": True, "text": text})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=503)


async def run_standalone() -> None:
    """Run the gateway as a standalone aiohttp service."""
    app = web.Application()
    app.router.add_get("/health", _aio_health)
    app.router.add_get("/ws", _aio_wss)
    app.router.add_get("/speak", _aio_speak)
    app.router.add_post("/speak", _aio_speak)
    app.router.add_post("/transcribe", _aio_transcribe)

    if not _port_available(BIND_HOST, BIND_PORT):
        _log("critical", f"Port {BIND_PORT} is already in use. Exiting.")
        raise SystemExit(1)

    _log("info", f"Starting Kai Voice Gateway on {BIND_HOST}:{BIND_PORT}")

    runner = web.AppRunner(app)
    await runner.setup()

    # TLS support — cert files shared with the API HTTPS server
    ssl_context: Optional[ssl.SSLContext] = None
    cert_path = Path(__file__).resolve().parents[2] / "certs" / "cert.pem"
    key_path = Path(__file__).resolve().parents[2] / "certs" / "key.pem"
    tls_env = os.environ.get("KAI_VOICE_TLS", "auto").strip().lower()
    use_tls = (tls_env == "on") or (tls_env == "auto" and cert_path.exists() and key_path.exists())
    if tls_env in ("off", "0", "false", "no"):
        use_tls = False
    if use_tls and cert_path.exists() and key_path.exists():
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(str(cert_path), str(key_path))
        _log("info", f"TLS enabled on port {BIND_PORT}")

    site = web.TCPSite(runner, BIND_HOST, BIND_PORT, ssl_context=ssl_context)
    await site.start()

    proto = "wss" if ssl_context else "ws"
    _log("info", f"Kai Voice Gateway running on {BIND_HOST}:{BIND_PORT}")
    _log("info", f"WSS: {proto}://{BIND_HOST}:{BIND_PORT}/ws")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        _log("info", "Shutting down Kai Voice Gateway")
    finally:
        await runner.cleanup()


async def _aio_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "kai-voice",
        "version": "1.0.0",
        "timestamp": _iso_now(),
    })


async def _aio_wss(request: web.Request) -> WebSocketResponse:
    """Bare aiohttp WSS handler for the standalone runner."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    session_id = _make_session_id()
    pipeline = VoicePipeline(ws, session_id=session_id)

    _log("info", f"[aio] WSS connection opened: session={session_id}")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                await pipeline.handle_binary(msg.data)
            elif msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    obj = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                event_type = obj.get("type", "")
                if event_type == "wake.detected":
                    await pipeline.handle_wake()
                elif event_type == "vad.stop":
                    await pipeline.handle_vad_stop(obj.get("data", {}).get("reason", "silence"))
                elif event_type == "tts.done":
                    await pipeline.handle_tts_done()
                elif event_type == "wake.interrupt":
                    await pipeline.handle_interrupt()
                elif event_type in ("ping", "ping_frame"):
                    await pipeline.handle_ping(b"")
    except Exception as exc:
        _log("error", f"[aio] WSS error {session_id}: {exc}")
    finally:
        _log("info", f"[aio] WSS closed: session={session_id}")

    return ws


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _make_session_id() -> str:
    import uuid
    return str(uuid.uuid4())[:8]


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _port_available(host: str, port: int) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=1):
            return False
    except OSError:
        return True


def _log(level: str, message: str) -> None:
    from core.logger import info, warning, error
    prefix = f"voice_gateway: {message}"
    if level == "info":
        info(prefix)
    elif level == "warn":
        warning(prefix)
    elif level == "error":
        error(prefix)
    elif level == "critical":
        error(prefix)


# ---------------------------------------------------------------------------
# CLI entry point (systemd service)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Kai Voice Gateway starting on {BIND_HOST}:{BIND_PORT}")
    asyncio.run(run_standalone())
