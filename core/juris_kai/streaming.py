"""Local-only streaming generation for Juris Kai.

Streams tokens from the local Ollama server (VM 104 P40, reached through the
SSH tunnel on ``127.0.0.1:11434``) using ``/api/chat`` with ``stream=True``.
That lets the Telegram bot show the first words in well under a second instead
of waiting ~19 s for a full non-streamed completion.

Strictly local: there is no cloud fallback here. ``stream_chat`` raises on
failure and the caller is expected to fall back to the existing blocking
``ai_router.delegate()`` path (which itself routes only to local providers).

This is a shared LLM primitive, so the prompt-injection guard lives **here**
(not at each call site): the inbound prompt is scanned + neutralized before it
reaches the model, the accumulated reply prefix is scanned incrementally and a
suspicious span aborts the stream via :class:`StreamGuardAbort` before it can
be rendered, and ``collect`` / ``generate`` guard the final text with
``guard_output``. Every caller -- the Juris bot, the Command Center test-query
endpoints -- is therefore covered by construction.
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


class StreamGuardAbort(RuntimeError):
    """Raised when the incremental output guard flags the streamed prefix.

    A :class:`RuntimeError` subclass so existing ``except Exception`` fallbacks
    still contain it; callers that care can read ``markers`` and fall back to
    the fully-guarded blocking path.
    """

    def __init__(self, markers, source: str = "juris_kai_stream"):
        self.markers = sorted({m for m in (markers or []) if m})
        self.source = source
        super().__init__(
            "stream aborted by injection guard: " + ", ".join(self.markers))


def available(timeout: float = 2.0) -> bool:
    """True if the local Ollama server answers."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Guard helpers (optional: an import/scan failure degrades to "no guard")
# ---------------------------------------------------------------------------

def _protected_fragments() -> list:
    """Juris system-prompt text that must never appear verbatim in a reply."""
    try:
        from core.juris_kai.prompt import (
            _PREAMBLE, _JURISDICTION_GATE, _DATABASE_FIRST,
        )
        return [_PREAMBLE, _JURISDICTION_GATE, _DATABASE_FIRST]
    except Exception:  # noqa: BLE001 - best effort
        return []


def _guard_prompt(prompt: str, source: str) -> str:
    """Neutralize instruction-like spans in an inbound prompt before dispatch."""
    if not prompt:
        return prompt
    try:
        from core.legal.injection import guard_input
        verdict = guard_input(prompt, source=source)
    except Exception as exc:  # noqa: BLE001 - guard is optional, never block
        logger.warning("injection input guard unavailable source=%s: %s",
                       source, exc)
        return prompt
    if verdict.get("suspected"):
        logger.warning("inbound injection neutralized source=%s markers=%s",
                       source, verdict.get("markers"))
        return verdict.get("clean_text", prompt)
    return prompt


def _guard_final(text: str, source: str) -> str:
    """Redact leaks in the final reply; safe fallback if tripped."""
    if not text:
        return text
    try:
        from core.legal.injection import guard_output
        verdict = guard_output(text, source=source,
                               protected=_protected_fragments())
    except Exception as exc:  # noqa: BLE001
        logger.warning("injection output guard unavailable source=%s: %s",
                       source, exc)
        return text
    return verdict.get("text", text)


# ---------------------------------------------------------------------------
# Raw transport
# ---------------------------------------------------------------------------

def _raw_stream_chat(prompt: str, model: str, read_timeout: float,
                     task_type: str):
    """Yield incremental text chunks straight from the local model."""
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


# ---------------------------------------------------------------------------
# Guarded public API
# ---------------------------------------------------------------------------

def stream_chat(prompt: str, task_type: str = "legal_research",
                model: str | None = None, timeout: float | None = None,
                guard: bool = True, source: str = "juris_kai_stream"):
    """Yield incremental text chunks from the local model.

    With ``guard`` (default) the inbound prompt is neutralized first, and after
    every chunk the accumulated prefix is scanned -- so a marker split across a
    chunk boundary is caught -- and :class:`StreamGuardAbort` is raised *before*
    the suspicious span can be yielded. Raises on any transport/parse failure so
    callers can fall back.
    """
    model = model or DEFAULT_MODEL
    read_timeout = timeout or READ_TIMEOUT
    if guard:
        prompt = _guard_prompt(prompt, source)

    guard_prefix = None
    if guard:
        try:
            from core.legal.injection import guard_stream_prefix
            guard_prefix = guard_stream_prefix
        except Exception as exc:  # noqa: BLE001 - degrade to unguarded transport
            logger.warning("injection stream guard unavailable: %s", exc)

    if guard_prefix is None:
        yield from _raw_stream_chat(prompt, model, read_timeout, task_type)
        return

    acc = ""
    for piece in _raw_stream_chat(prompt, model, read_timeout, task_type):
        acc += piece
        verdict = guard_prefix(acc, source=source)
        if verdict.get("abort"):
            raise StreamGuardAbort(verdict.get("markers"), source)
        yield piece


def collect(chunks, source: str = "juris_kai_stream", guard: bool = True) -> str:
    """Join a chunk iterator into the full response string.

    The final text is passed through ``guard_output`` so an outbound leak is
    redacted / replaced with the safe fallback even if it only becomes complete
    once the stream is fully collected.
    """
    text = "".join(chunks)
    return _guard_final(text, source) if guard else text


def generate(prompt: str, task_type: str = "legal_research",
             model: str | None = None, timeout: float | None = None,
             guard: bool = True, source: str = "juris_kai_stream") -> str:
    """Non-streamed local generation (same model/options as stream_chat).

    Used for latency comparisons and the Command Center test-query box.
    """
    return collect(stream_chat(prompt, task_type=task_type, model=model,
                               timeout=timeout, guard=guard, source=source),
                   source=source, guard=guard)
