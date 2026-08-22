"""Kai Betting — single reusable GPU.ai inference client.

One OpenAI-compatible client for ALL three models. The model id is supplied
dynamically so models can be swapped without touching the betting system.

GPU.ai is pay-per-use: this client only ever makes an HTTP request when
explicitly told to; it keeps no process, pool, or connection warm.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Optional, Dict, Any, List

import requests

from core.kai_betting.ai.prompts import MODELS

logger = logging.getLogger(__name__)

# Env-driven configuration. NEVER hard-code the key; the key is never logged
# or returned, and Authorization headers are never logged.
BASE_URL = os.environ.get("GPUAI_BASE_URL", "https://api.gpu.ai/v1")
API_KEY = os.environ.get("GPUAI_API_KEY", "")

_CONNECT_TIMEOUT = float(os.environ.get("GPUAI_CONNECT_TIMEOUT", "10"))
_RESPONSE_TIMEOUT = float(os.environ.get("GPUAI_RESPONSE_TIMEOUT", "60"))
_MAX_RETRIES = int(os.environ.get("GPUAI_MAX_RETRIES", "2"))


class GPUAIError(Exception):
    """Base error for GPU.ai inference failures."""


class GPUAIInferenceError(GPUAIError):
    """The model returned no valid result after retries."""


class GPUAIUnavailableError(GPUAIError):
    """The client is not configured (missing API key / base URL)."""


class GPUAIResponse:
    """A validated inference result with usage/cost metadata."""

    def __init__(self, data: Dict[str, Any], model_key: str, input_tokens: int,
                 output_tokens: int, latency_ms: int, request_id: str):
        self.data = data
        self.model_key = model_key
        self.model_id = MODELS[model_key]["model_id"]
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.request_id = request_id
        self.estimated_cost = self._cost(model_key, input_tokens, output_tokens)

    @staticmethod
    def _cost(model_key: str, in_tokens: int, out_tokens: int) -> float:
        m = MODELS[model_key]
        return (
            in_tokens * m["input_price_per_mtok"]
            + out_tokens * m["output_price_per_mtok"]
        ) / 1_000_000


class GPUAIClient:
    """One client, three models. Model id is passed per call."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else API_KEY

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def chat_json(
        self,
        model_key: str,
        system_prompt: str,
        user_payload: Any,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
        request_id: Optional[str] = None,
    ) -> GPUAIResponse:
        """Run one structured chat completion and return a validated object.

        Args:
            model_key: one of 'qwen' | 'deepseek' | 'k3'.
            system_prompt: the versioned tier prompt.
            user_payload: a JSON-serializable evidence package.
            max_tokens: output cap (defaults to the model's configured cap).
            request_id: caller-supplied correlation id (defaults to a fresh uuid).
        """
        if not self.configured:
            raise GPUAIUnavailableError("GPUAI_API_KEY not configured")

        model = MODELS[model_key]
        model_id = model["model_id"]
        max_tokens = max_tokens or model["max_output"]
        request_id = request_id or uuid.uuid4().hex

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, default=str)},
        ]
        payload = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=(_CONNECT_TIMEOUT, _RESPONSE_TIMEOUT),
                )
                latency_ms = int((time.monotonic() - start) * 1000)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise GPUAIError(f"upstream {resp.status_code}")
                if resp.status_code >= 400:
                    # 4xx (other than 429) is non-retryable.
                    raise GPUAIInferenceError(f"upstream {resp.status_code}: {resp.text[:300]}")
                body = resp.json()
                content = (
                    (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
                )
                data = self._extract_json(content)
                usage = body.get("usage") or {}
                in_tok = int(usage.get("prompt_tokens") or 0)
                out_tok = int(usage.get("completion_tokens") or 0)
                return GPUAIResponse(data, model_key, in_tok, out_tok, latency_ms, request_id)
            except GPUAIInferenceError:
                raise
            except (requests.RequestException, GPUAIError, ValueError) as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    time.sleep(1.0 * (attempt + 1))
                continue

        raise GPUAIInferenceError(f"inference failed after retries: {last_error}")

    @staticmethod
    def _extract_json(content: str) -> Dict[str, Any]:
        """Parse a model completion as JSON, tolerating markdown fences."""
        text = (content or "").strip()
        if text.startswith("```"):
            # strip a ```json ... ``` fence
            text = text.split("```", 2)[1] if "```" in text[3:] else text
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()
        elif text.startswith("```json"):
            text = text[7:].strip().rstrip("```").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"non-JSON model output: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("model output was not a JSON object")
        return data


def estimate_cost(model_key: str, input_tokens: int, output_tokens: int) -> float:
    m = MODELS[model_key]
    return (
        input_tokens * m["input_price_per_mtok"]
        + output_tokens * m["output_price_per_mtok"]
    ) / 1_000_000
