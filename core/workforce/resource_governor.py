"""§18 Resource Governor — bound workforce growth by real resource state.

KAI must not create unlimited workers. Before a *new* teammate is
materialized the Factory consults this governor: reuse an existing capable
worker first (the Factory already does), and refuse to grow the workforce past
a configured cap. The governor also exposes a live snapshot (workers,
CPU/RAM/load, GPU-arbiter permits, mission queue depth) so the Command Center
and the scheduler can reason about resource pressure.

Reuses the existing Teammate Registry (worker identity) and GPU arbiter
(inference permits) — it introduces no second registry or scheduler.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

SOURCE = "resource_governor"

# Deliberately generous default: the governor is a safety cap, not a tuning
# knob. Operators can lower KAI_MAX_WORKERS for constrained hosts.
DEFAULT_MAX_WORKERS = 64

# Terminal / inactive lifecycle states that do not count against the cap.
_INACTIVE_STATES = frozenset({"RETIRED", "COMPLETED"})


class ResourceExhausted(RuntimeError):
    """Raised when a new worker is requested but the workforce is at its cap."""


def _resource_probe() -> dict:
    """Best-effort host resource probe. Never raises; empty when psutil is absent."""
    probe: dict[str, Any] = {}
    try:
        import psutil  # type: ignore
        probe["cpu_percent"] = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        probe["mem_percent"] = mem.percent
        probe["mem_available_mb"] = int(mem.available / (1024 * 1024))
    except Exception:  # pragma: no cover - psutil optional
        pass
    try:
        probe["load_avg"] = list(os.getloadavg())
    except Exception:  # pragma: no cover - not on all platforms
        pass
    return probe


class ResourceGovernor:
    def __init__(self, max_workers: Optional[int] = None) -> None:
        env = os.environ.get("KAI_MAX_WORKERS", str(DEFAULT_MAX_WORKERS))
        try:
            cap = int(max_workers if max_workers is not None else env)
        except (TypeError, ValueError):
            cap = DEFAULT_MAX_WORKERS
        self.max_workers = max(1, cap)

    # -- admission ----------------------------------------------------------
    def active_count(self, registry: Any) -> int:
        if registry is None:
            return 0
        return sum(1 for t in registry.list()
                   if getattr(t, "status", None) not in _INACTIVE_STATES)

    def can_spawn(self, registry: Any) -> tuple[bool, str]:
        """Return (allowed, reason). Reuse should be attempted before this."""
        total = self.active_count(registry)
        if total >= self.max_workers:
            return False, (f"worker cap reached ({total}/{self.max_workers}); "
                           "reuse an existing worker or retire idle ones")
        return True, ""

    # -- observability ------------------------------------------------------
    def snapshot(self, registry: Any = None, missions: Any = None) -> dict:
        workers: dict[str, Any] = {"active": 0, "idle": 0, "busy": 0,
                                   "failed": 0, "total": 0}
        if registry is not None:
            records = registry.list()
            active = [t for t in records
                      if getattr(t, "status", None) not in _INACTIVE_STATES]
            workers = {
                "total": len(records),
                "active": len(active),
                "idle": sum(1 for t in active
                            if getattr(t, "status", None) == "READY"),
                "busy": sum(1 for t in active
                            if getattr(t, "status", None) in ("ASSIGNED", "EXECUTING")),
                "failed": sum(1 for t in active
                              if getattr(t, "health", "HEALTHY") == "FAILED"),
            }

        queue_depth = 0
        try:
            if missions is not None:
                queue_depth = sum(
                    1 for m in missions
                    if str(m.get("status", "")).upper()
                    not in ("COMPLETED", "FAILED", "CANCELLED"))
        except Exception:  # pragma: no cover - missions is a best-effort signal
            queue_depth = 0

        gpu: dict[str, Any] = {}
        try:
            from core.ai.gpu_arbiter import gpu_arbiter
            gpu = gpu_arbiter.stats()
        except Exception:  # pragma: no cover
            gpu = {}

        allowed, reason = self.can_spawn(registry) if registry is not None else (True, "")
        return {
            "schema": 1,
            "max_workers": self.max_workers,
            "workers": workers,
            "queue_depth": queue_depth,
            "gpu": gpu,
            "host": _resource_probe(),
            "can_spawn": allowed,
            "reason": reason,
        }


governor = ResourceGovernor()
