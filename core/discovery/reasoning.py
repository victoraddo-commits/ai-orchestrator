"""Pluggable reasoning backends for site classification and recipe learning.

The production backend is :class:`LocalModelReasoningBackend`, wired to the Kai
local model fabric (Ollama ``qwen3-coder:kai`` on VM104). It reuses the existing
router client :func:`core.ai.ai_router.delegate`, which already routes reasoning
tasks across the local fabric. The backend is **disabled by default** and only
imports / calls the router when explicitly enabled, so importing this module and
running the default pipeline never touches the network and never depends on a
model for correctness. :class:`FixtureReasoningBackend` provides deterministic,
fully offline proposals for the test suite.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional, Protocol, runtime_checkable

from core.site_recipes.schema import SiteProfile

#: Default Kai local model (VM104, Tesla P40) and the Ollama endpoint it serves.
DEFAULT_MODEL = "qwen3-coder:kai"
DEFAULT_ENDPOINT = os.environ.get("KAI_OLLAMA_URL", "http://127.0.0.1:11434")

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@runtime_checkable
class ReasoningBackend(Protocol):
    """A best-effort enrichment hook; ``None`` means "no opinion"."""

    def propose_profile(self, domain: str, target: str) -> Optional[dict]:
        ...

    def propose_recipe(self, profile: SiteProfile, snapshot: dict) -> Optional[dict]:
        ...


class FixtureReasoningBackend:
    """Deterministic in-memory backend keyed by domain (tests only)."""

    def __init__(self, profiles: Optional[dict] = None,
                 recipes: Optional[dict] = None):
        self.profiles = dict(profiles or {})
        self.recipes = dict(recipes or {})

    def propose_profile(self, domain: str, target: str) -> Optional[dict]:
        return self.profiles.get(domain)

    def propose_recipe(self, profile: SiteProfile, snapshot: dict) -> Optional[dict]:
        return self.recipes.get(profile.domain)


class LocalModelReasoningBackend:
    """Kai local model fabric backend (VM104 ``qwen3-coder:kai``).

    Wiring: prompts (sanitized -- no secrets) are handed to
    :func:`core.ai.ai_router.delegate` with ``task_type="text_task"``; the router
    selects a provider chain that terminates on the local fabric (Ollama on
    VM104 at :data:`DEFAULT_ENDPOINT`). The response must be a strict JSON
    object; anything else yields ``None`` so callers fall back to heuristics.

    ``enabled`` defaults to ``False``: when disabled the backend is a pure
    no-op and never imports the router or opens a socket. Tests inject a
    ``delegate`` callable to exercise the enabled path without any network.
    """

    def __init__(self, model: str = DEFAULT_MODEL,
                 endpoint: str = DEFAULT_ENDPOINT,
                 enabled: bool = False,
                 delegate: Optional[Callable[..., object]] = None):
        self.model = model
        self.endpoint = endpoint
        self.enabled = bool(enabled)
        self._delegate = delegate

    def _call(self, prompt: str) -> object:
        if self._delegate is not None:
            return self._delegate(prompt, task_type="text_task")
        # Lazy import so merely importing this module never pulls in the
        # router (and never touches the network) unless the backend is enabled.
        from core.ai.ai_router import delegate
        return delegate(prompt, task_type="text_task")

    def _propose(self, prompt: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            raw = self._call(prompt)
        except Exception:
            return None
        if not isinstance(raw, str):
            return None
        match = _JSON_OBJECT_RE.search(raw)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def propose_profile(self, domain: str, target: str) -> Optional[dict]:
        prompt = (
            "You classify website signup flows. Return ONLY a JSON object with "
            "optional keys: flow_type (single_page|multi_step|sso_only|"
            "invite_only|unknown), requirements (object of booleans: email, "
            "phone, captcha, mfa, payment, kyc, email_verify, phone_verify), "
            "confidence (0..1). Never include secrets.\n"
            f"domain: {domain}\nurl: {target}\n"
        )
        return self._propose(prompt)

    def propose_recipe(self, profile: SiteProfile, snapshot: dict) -> Optional[dict]:
        prompt = (
            "Propose a secret-free registration recipe as a strict JSON object "
            "with keys flow_type, steps, fields, requirements. Use symbolic "
            "value sources only (identity.email, generated_password, ...).\n"
            f"domain: {getattr(profile, 'domain', '')}\n"
            f"snapshot: {json.dumps(snapshot, default=str)[:4000]}\n"
        )
        return self._propose(prompt)


__all__ = ["ReasoningBackend", "FixtureReasoningBackend",
           "LocalModelReasoningBackend", "DEFAULT_MODEL", "DEFAULT_ENDPOINT"]
