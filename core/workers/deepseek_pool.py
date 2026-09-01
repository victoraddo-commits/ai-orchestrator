"""DeepSeek worker pool — concurrent AI worker management for Kai.

Per operator directive 2026-08-09: DeepSeek is PRIMARY across all roles.
This pool manages concurrent DeepSeek workers that execute build-phase tasks
in parallel, with results routed to Telegram.

Capacity: 8 default concurrent workers (~60 RPM DeepSeek soft limit / ~7.5
calls-per-worker-per-cycle). Each worker is a lightweight coroutine that
pulls tasks from a shared queue and executes them against DeepSeek's native
API (text tasks) or via OmniRoute's coding gateway.

The pool is designed to work WITHIN Kai's existing ThreadPoolExecutor (8
build slots in config/providers.yaml) — it doesn't compete for concurrency;
it replaces the synchronous delegate() calls within each build slot with
parallel DeepSeek workers, so a single build's planning/architecture/review
phases all run on DeepSeek concurrently.
"""

import threading
import time
import queue
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable

# Lazy imports to avoid circular deps with ai_router / build_manager
import core.ai.ai_router as ai_router
import core.telegram_bridge as telegram_bridge
from core.logger import info as _log


# --- Configuration ---

# Default concurrent workers — tuned for DeepSeek's ~60 RPM soft limit:
# 8 workers × 7.5 calls/cycle = 60 RPM max, safe margin below the limit.
DEFAULT_WORKERS = 8

# How long a worker waits for a new task before the pool considers it idle.
IDLE_POLL_SECONDS = 2.0


# --- Data types ---

@dataclass
class WorkerTask:
    """A unit of work submitted to the DeepSeek pool."""

    task_id: str
    task_type: str  # "planning", "architecture", "review", "coding", etc.
    prompt: str
    build_id: Optional[str] = None
    build_name: Optional[str] = None
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class WorkerResult:
    """Result of a DeepSeek worker task."""

    task_id: str
    task_type: str
    provider: str
    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --- Pool ---

class DeepSeekWorkerPool:
    """Thread-safe pool of concurrent DeepSeek workers.

    Usage:
        pool = DeepSeekWorkerPool(workers=8)
        pool.start()
        task_id = pool.submit("planning", "Design a REST API for...", build_id="abc123")
        # ... work happens in background ...
        status = pool.status()  # {active, queued, completed, failed, workers}
        pool.stop()
    """

    def __init__(self, workers: int = DEFAULT_WORKERS, telegram_chat_id: Optional[str] = None):
        self._max_workers = workers
        self._queue: queue.Queue[WorkerTask] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._lock = threading.Lock()

        # Stats
        self._active_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._results: list[WorkerResult] = []

        # Telegram delivery
        self._telegram_chat_id = telegram_chat_id or telegram_bridge.ALLOWED_CHAT_ID

        # Callbacks — set by integrator
        self.on_result: Optional[Callable[[WorkerResult], None]] = None

    # --- Lifecycle ---

    def start(self):
        """Launch worker threads. Idempotent — no-op if already running."""
        with self._lock:
            if self._running:
                return
            self._running = True

        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                args=(i,),
                name=f"ds-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

        _log(f"DeepSeek worker pool started: {self._max_workers} workers")

    def stop(self, drain: bool = True):
        """Stop all workers. If drain=True, process queued tasks first."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        if drain:
            self._queue.join()  # wait for queued tasks to finish

        # Wake idle workers so they exit
        for _ in self._threads:
            self._queue.put(_SENTINEL)

        for t in self._threads:
            t.join(timeout=10)
        self._threads.clear()

        _log(f"DeepSeek worker pool stopped: completed={self._completed_count} failed={self._failed_count}")

    # --- Task submission ---

    def submit(self, task_type: str, prompt: str, build_id: Optional[str] = None, build_name: Optional[str] = None) -> str:
        """Enqueue a task. Returns task_id for tracking."""
        task_id = f"ds-{uuid.uuid4().hex[:8]}"
        task = WorkerTask(
            task_id=task_id,
            task_type=task_type,
            prompt=prompt,
            build_id=build_id,
            build_name=build_name,
        )
        self._queue.put(task)
        return task_id

    # --- Status ---

    def status(self) -> dict:
        """Return a snapshot of pool health and stats."""
        with self._lock:
            return {
                "running": self._running,
                "workers": self._max_workers,
                "active": self._active_count,
                "queued": self._queue.qsize(),
                "completed": self._completed_count,
                "failed": self._failed_count,
                "uptime_seconds": None,  # TODO
            }

    # --- Internals ---

    def _worker_loop(self, worker_idx: int):
        """Main loop for a single worker thread."""
        while True:
            try:
                task = self._queue.get(timeout=IDLE_POLL_SECONDS)
            except queue.Empty:
                # Check if we should exit
                with self._lock:
                    if not self._running:
                        break
                continue

            if task is _SENTINEL:
                self._queue.task_done()
                break

            with self._lock:
                self._active_count += 1

            result = self._execute(task)

            with self._lock:
                self._active_count -= 1
                if result.success:
                    self._completed_count += 1
                else:
                    self._failed_count += 1
                self._results.append(result)
                while len(self._results) > MAX_RESULTS:
                    self._results.pop(0)

            # Notify
            self._notify_result(result)

            if self.on_result:
                try:
                    self.on_result(result)
                except Exception as e:
                    _log(f"DeepSeek pool: on_result callback raised: {type(e).__name__}: {e}")

            self._queue.task_done()

            with self._lock:
                if not self._running and self._queue.empty():
                    break

        with self._lock:
            if self._active_count > 0:
                self._active_count -= 1

    def _execute(self, task: WorkerTask) -> WorkerResult:
        """Execute a single task against DeepSeek. Tries Pro first, then Flash."""
        start = time.time()

        # Try DeepSeek Pro first (full model, best quality)
        for attempt_provider in ("deepseek_native_pro", "deepseek_native_flash"):
            try:
                result = ai_router.delegate(
                    task.prompt,
                    task_type=task.task_type,
                    capability="text_task",
                    timeout=120,
                )
                duration_ms = int((time.time() - start) * 1000)
                return WorkerResult(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    provider=result["provider"],
                    success=True,
                    response=str(result["response"])[:2000],  # truncate for memory
                    duration_ms=duration_ms,
                )
            except ai_router.AllProvidersFailed as e:
                # First attempt failed — log and try fallback
                if attempt_provider == "deepseek_native_pro":
                    _log(
                        f"ds-worker task {task.task_id}: Pro failed, trying Flash — "
                        + "; ".join(a["error"][:60] for a in (e.attempts or [])[:2])
                    )
                else:
                    # Both Pro and Flash exhausted
                    duration_ms = int((time.time() - start) * 1000)
                    return WorkerResult(
                        task_id=task.task_id,
                        task_type=task.task_type,
                        provider="deepseek_native_pro",
                        success=False,
                        error=f"All DeepSeek providers exhausted: "
                        + "; ".join(a["error"][:80] for a in (e.attempts or [])[:3]),
                        duration_ms=duration_ms,
                    )
            except Exception as e:
                if attempt_provider == "deepseek_native_flash":
                    duration_ms = int((time.time() - start) * 1000)
                    return WorkerResult(
                        task_id=task.task_id,
                        task_type=task.task_type,
                        provider=attempt_provider,
                        success=False,
                        error=str(e)[:200],
                        duration_ms=duration_ms,
                    )

        duration_ms = int((time.time() - start) * 1000)
        return WorkerResult(
            task_id=task.task_id,
            task_type=task.task_type,
            provider="unknown",
            success=False,
            error="unreachable in execute()",
            duration_ms=duration_ms,
        )

    def _notify_result(self, result: WorkerResult):
        """Send result notification to Telegram if significant."""
        # Only notify on failures or for monitoring tasks
        if result.success:
            return  # silent on success — reduces noise

        # Gate: max 1 failure notification per 60s to avoid spam
        now = time.time()
        if not hasattr(self, "_last_failure_notify"):
            self._last_failure_notify = 0.0
        if now - self._last_failure_notify < 60:
            return
        self._last_failure_notify = now

        try:
            telegram_bridge.send_message(
                f"⚠️ DeepSeek worker failed\n"
                f"Task: {result.task_id} ({result.task_type})\n"
                f"Error: {result.error or 'unknown'}\n"
                f"Duration: {result.duration_ms}ms",
                chat_id=self._telegram_chat_id,
            )
        except Exception as e:
            _log(f"DeepSeek pool: failed to send Telegram failure notification: {type(e).__name__}")


# Sentinel to wake idle workers for graceful shutdown.
_SENTINEL = object()


# --- Module-level singleton ---

_pool: Optional[DeepSeekWorkerPool] = None
_pool_lock = threading.Lock()


def get_pool(workers: int = DEFAULT_WORKERS) -> DeepSeekWorkerPool:
    """Get or create the module-level DeepSeek worker pool singleton."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = DeepSeekWorkerPool(workers=workers)
        return _pool


def start_pool(workers: int = DEFAULT_WORKERS):
    """Start the singleton pool. Used by scheduler on startup."""
    pool = get_pool(workers)
    pool.start()
    return pool


def stop_pool():
    """Stop the singleton pool. Used by scheduler on shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.stop()
            _pool = None
