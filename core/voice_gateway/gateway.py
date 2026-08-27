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
import json
import os
import signal
import time
from typing import Optional

import aiohttp
from aiohttp import web
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from core.voice_gateway import events as ev
from core.voice_gateway.pipeline import VoicePipeline


# Configuration
BIND_HOST = os.environ.get("KAI_VOICE_BIND", "127.0.0.1")
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
            # Binary audio frame
            if ws.state == WebSocketState.CONNECTED:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=60)
                except asyncio.TimeoutError:
                    continue

                if msg.type == aiohttp.WSMsgType.BINARY:
                    audio_bytes = msg.data
                    if len(audio_bytes) <= MAX_BINARY_FRAME:
                        await pipeline.handle_binary(audio_bytes)

                elif msg.type == aiohttp.WSMsgType.TEXT:
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

                elif msg.type == aiohttp.WSMsgType.CLOSE:
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

async def run_standalone() -> None:
    """Run the gateway as a standalone aiohttp service."""
    app = web.Application()
    app.router.add_get("/health", _aio_health)
    app.router.add_get("/ws", _aio_wss)

    if not _port_available(BIND_HOST, BIND_PORT):
        _log("critical", f"Port {BIND_PORT} is already in use. Exiting.")
        raise SystemExit(1)

    _log("info", f"Starting Kai Voice Gateway on {BIND_HOST}:{BIND_PORT}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, BIND_HOST, BIND_PORT)
    await site.start()

    _log("info", f"Kai Voice Gateway running on {BIND_HOST}:{BIND_PORT}")
    _log("info", f"WSS: ws://{BIND_HOST}:{BIND_PORT}/ws")

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
