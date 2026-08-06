"""Kai V3 Scheduler — Entry point for the v3 orchestration service.

Replaces core/scheduler.py. Same pattern: 60s loop, watchdog heartbeat,
systemd integration. Uses the v3 cycle instead of the old orchestrator_cycle.

Deploy by switching the systemd service ExecStart to point here.
"""

import threading
import time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

try:
    from systemd.daemon import notify
except ImportError:
    notify = None

load_dotenv()

from core.v3.cycle import run_cycle
from core.logger import info, error as log_error

INTERVAL = 60
WATCHDOG_PING_INTERVAL = 30


class WatchdogHeartbeat:
    """Pings systemd's watchdog on a fixed interval from a background thread.

    Started before run_cycle() and stopped once it returns, so a long-running
    but alive cycle keeps the watchdog fed.
    """

    def __init__(self, interval=None, notify_fn=None):
        self.interval = interval or WATCHDOG_PING_INTERVAL
        self.notify_fn = notify_fn
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        fn = self.notify_fn if self.notify_fn is not None else notify
        if fn is None:
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(fn,), name="watchdog-heartbeat", daemon=True,
        )
        self._thread.start()

    def _run(self, fn):
        while not self._stop.is_set():
            try:
                fn("WATCHDOG=1")
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False


def start():
    """Main scheduler loop. Runs forever, cycling every INTERVAL seconds."""

    info("Kai V3 scheduler started")

    if notify:
        notify("READY=1")

    while True:
        heartbeat = WatchdogHeartbeat()
        heartbeat.start()

        try:
            result = run_cycle()

            info(
                f"V3 cycle {result.get('cycle', '?')} completed "
                f"({result.get('duration_s', 0):.1f}s, "
                f"{result.get('build_summary', {}).get('completed', 0)} completed)"
            )

            Path("/var/lib/ai-orchestrator/heartbeat").write_text(
                datetime.now(timezone.utc).isoformat()
            )

            if notify:
                notify("WATCHDOG=1")

        except Exception as e:
            log_error(f"V3 scheduler error: {type(e).__name__}: {e}")

        finally:
            heartbeat.stop()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    start()
