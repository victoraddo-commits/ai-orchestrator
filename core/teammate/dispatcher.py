"""Tiered teammate dispatcher — fastest-capable-worker routing.

Resolves a teammate skill to a provider chain (via the model fabric), ranks it
T0-first, runs accelerated calls inside the GPU arbiter, and uses a tier-aware
timeout per provider. Fails through the chain; never calls a slower tier while
a faster healthy one is available.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from core.teammate.speed_tiers import estimate_timeout, rank_candidates, tier_of

logger = logging.getLogger(__name__)


class DispatchError(RuntimeError):
    """Raised when every provider in the resolved chain failed."""


class TieredDispatcher:
    def __init__(self, resolve_plan: Optional[Callable] = None,
                 runner: Optional[Callable] = None, arbiter: Any = None,
                 gpu_slot_timeout: float = 300.0) -> None:
        self._resolve_plan = resolve_plan
        self._runner = runner
        self._arbiter = arbiter
        self.gpu_slot_timeout = gpu_slot_timeout

    def _chain(self, teammate_id, skill_id, skill_registry):
        if self._resolve_plan is not None:
            plan = self._resolve_plan(teammate_id, skill_id, skill_registry)
        else:
            from core.teammate.model_fabric import resolve_model_plan
            plan = resolve_model_plan(teammate_id, skill_id, skill_registry)
        chain = getattr(plan, "provider_chain", plan)
        return list(chain or [])

    def dispatch(self, teammate_id: str, skill_id: str, skill_registry: Any,
                 instruction: str, project_path: Optional[str] = None,
                 estimated_tokens: int = 2000) -> Any:
        chain = rank_candidates(self._chain(teammate_id, skill_id, skill_registry))
        if not chain:
            raise DispatchError(f"no provider resolved for skill {skill_id}")
        if self._runner is None:
            raise DispatchError("dispatcher has no runner configured")

        last_error = None
        for provider in chain:
            tier = tier_of(provider)
            timeout = estimate_timeout(provider, estimated_tokens)
            try:
                if tier == "T0" and self._arbiter is not None:
                    with self._arbiter.slot(timeout=self.gpu_slot_timeout):
                        return self._call(provider, instruction, timeout, project_path)
                return self._call(provider, instruction, timeout, project_path)
            except Exception as exc:  # noqa: BLE001 - fail through the chain
                last_error = exc
                logger.warning("dispatch %s skill=%s provider=%s failed: %s",
                               teammate_id, skill_id, provider, exc)
        raise DispatchError(
            f"all providers failed for skill {skill_id}: {last_error}")

    def _call(self, provider, instruction, timeout, project_path):
        return self._runner(provider, instruction,
                            timeout=timeout, project_path=project_path)
