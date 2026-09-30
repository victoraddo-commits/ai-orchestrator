"""GPU inference arbiter — bounds concurrent accelerated (T0) model calls.

One P40 serves every Kai model call. Unbounded concurrency (builds × Ollama
parallel slots) thrashes it: per-request throughput collapses and generation
blows the build timeout. This arbiter is the single choke point — every T0
call runs inside a permit, so the GPU is never oversubscribed.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class GpuBusy(TimeoutError):
    """Raised when the arbiter cannot grant a permit within the timeout."""


class GpuArbiter:
    def __init__(self, permits: int = 2) -> None:
        permits = max(1, int(permits))
        self._permits = permits
        self._sem = threading.BoundedSemaphore(permits)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._waiting = 0
        self._max_wait_ms = 0.0

    @property
    def permits(self) -> int:
        return self._permits

    @contextmanager
    def slot(self, timeout: float | None = None):
        start = time.monotonic()
        with self._lock:
            self._waiting += 1
        acquired = self._sem.acquire(timeout=timeout)
        with self._lock:
            self._waiting -= 1
            if acquired:
                self._in_flight += 1
                wait_ms = (time.monotonic() - start) * 1000.0
                self._max_wait_ms = max(self._max_wait_ms, wait_ms)
            else:
                in_flight = self._in_flight
        if not acquired:
            raise GpuBusy(
                f"GPU arbiter saturated (permits={self._permits}, "
                f"in_flight={in_flight})")
        try:
            yield self
        finally:
            with self._lock:
                self._in_flight -= 1
            self._sem.release()

    def stats(self) -> dict:
        with self._lock:
            return {
                "permits": self._permits,
                "in_flight": self._in_flight,
                "waiting": self._waiting,
                "max_wait_ms": round(self._max_wait_ms, 1),
            }


def _default_permits() -> int:
    try:
        return max(1, int(os.environ.get("KAI_GPU_PERMITS", "2")))
    except (ValueError, TypeError):
        return 2


gpu_arbiter = GpuArbiter(_default_permits())
