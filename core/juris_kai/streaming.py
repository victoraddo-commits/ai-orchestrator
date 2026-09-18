"""Local-only streaming generation for Juris Kai.

Streams tokens from the local Ollama server (VM 104 P40, reached through the
SSH tunnel on ``127.0.0.1:11434``) using ``/api/chat`` with ``stream=True``.
That lets the Telegram bot show the first words in well under a second instead
of waiting ~19 s for a full non-streamed completion.

Strictly local: there is no cloud fallback here. ``stream_chat`` raises on
failure and the caller is expected to fall back to the existing blocking
``ai_router.delegate()`` path (which itself routes only to local providers).
"""

from __future__ import annotations

import json
import logging
import os

import requests

from core.juris_kai.prompt import budget_for

logger = logging.getLogger("juris_kai.streaming")

OLLAMA_URL = os.environ.get("JURIS_KAI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("JURIS_KAI_MODEL", "qwen3-coder:kai")
CONNECT_TIMEOUT = float(os.environ.get("JURIS_KAI_STREAM_CONNECT_TIMEOUT", "3"))
READ_TIMEOUT = float(os.environ.get("JURIS_KAI_STREAM_READ_TIMEOUT", "300"))
TEMPERATURE = float(os.environ.get("JURIS_KAI_TEMPERATURE", "0.7"))


def available(timeout: float = 2.0) -> bool:
    """True if the local Ollama server answers."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def stream_chat(prompt: str, task_type: str = "legal_research",
                model: str | None = None, timeout: float | None = None):
    """Yield incremental text chunks from the local model.

    Raises on any transport/parse failure so callers can fall back.
    """
    model = model or DEFAULT_MODEL
    read_timeout = timeout or READ_TIMEOUT
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "options": {
            "temperature": TEMPERATURE,
            "top_p": 0.9,
            "num_predict": budget_for(task_type),
        },
    }
    with requests.post(f"{OLLAMA_URL}/api/chat", json=payload, stream=True,
                       timeout=(CONNECT_TIMEOUT, read_timeout)) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"ollama stream error: {data['error']}")
            piece = ((data.get("message") or {}) if isinstance(data, dict) else {}).get("content") or ""
            if piece:
                yield piece
            if isinstance(data, dict) and data.get("done"):
                break


def collect(chunks) -> str:
    """Join a chunk iterator into the full response string."""
    return "".join(chunks)


def generate(prompt: str, task_type: str = "legal_research",
             model: str | None = None, timeout: float | None = None) -> str:
    """Non-streamed local generation (same model/options as stream_chat).

    Used for latency comparisons and the Command Center test-query box.
    """
    return collect(stream_chat(prompt, task_type=task_type, model=model,
                               timeout=timeout))
