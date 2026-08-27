"""ElevenLabs Flash v2.5 TTS client — cloud TTS for substantive answers.

Flash v2.5 is ElevenLabs' low-latency model designed for real-time applications.
Used only for substantive answers (acknowledgements go through Piper instead).

API key is read from ELEVENLABS_API_KEY env var.
"""

from __future__ import annotations

import os
from typing import Generator, Optional

import aiohttp


ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "pFZPqxYdaW8aqlX9ArmG")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "flash_v2.5")
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"


async def synthesize_stream(
    text: str,
    voice_id: Optional[str] = None,
    model: Optional[str] = None,
) -> Generator[bytes, None, None]:
    """Stream TTS audio from ElevenLabs.

    Yields raw MP3 or PCM chunks as they arrive.
    """
    api_key = ELEVENLABS_API_KEY
    if not api_key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY environment variable is not set. "
            "Set it to your ElevenLabs API key."
        )

    voice = voice_id or ELEVENLABS_VOICE_ID
    model_name = model or ELEVENLABS_MODEL

    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice}/stream"

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_name,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"ElevenLabs synthesis failed [{resp.status}]: {body[:200]}"
                )
            async for chunk in resp.content.iter_chunked(8192):
                if chunk:
                    yield chunk


async def synthesize_to_file(
    text: str,
    output_path: str,
    voice_id: Optional[str] = None,
) -> str:
    """Synthesize text and save to a file. Returns the file path."""
    chunks = []
    async for chunk in synthesize_stream(text, voice_id=voice_id):
        chunks.append(chunk)

    import aiofiles
    async with aiofiles.open(output_path, "wb") as f:
        for chunk in chunks:
            await f.write(chunk)

    return output_path
