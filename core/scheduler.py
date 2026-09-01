import json
import os
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

try:
    from systemd.daemon import notify
except ImportError:
    notify = None

# Explicit rather than relying on tools/proxmox.py's own load_dotenv() call
# as an import-order side effect -- this is the scheduler's true entrypoint.
load_dotenv()

from core.orchestrator_cycle import run_cycle
from core.logger import info
from core.workers.deepseek_pool import start_pool, stop_pool
from core.workers.telegram_monitor import TelegramMonitor
from core.network_discovery_cycle import run_network_discovery_cycle

# Worker pool, Telegram monitor, and Health Worker — started on first cycle,
# survive until shutdown.
_pool = None
_monitor = None
_health_worker = None
_last_network_discovery: float = 0.0
_network_discovery_interval = 300.0  # seconds — network discovery runs every 5 min


# Was 300s, tuned for infra health checks (Phase 1-11) where that cadence is
# fine. The build/roadmap pipeline (Phase 12C/12L) inherited this same cycle
# and 5-minute waits between build-progression steps were consistently the
# biggest source of "why is nothing happening" during live testing. Cheap
# reads (Proxmox/Docker checks) at 5x the frequency cost effectively nothing.
INTERVAL = 60

# TK-b7614289: Pause file checked at top of every cycle.  API endpoint
# /api/admin/pause-scheduler creates it, /api/admin/resume-scheduler removes it.
SCHEDULER_PAUSE_FILE = Path(os.environ.get(
    "SCHEDULER_PAUSE_FILE",
    "/project/ai-orchestrator/memory/scheduler_paused.json",
))

# 13P: run_cycle() blocks synchronously on coding-agent subprocess calls that
# can legitimately run many minutes. The systemd unit's WatchdogSec is 600s,
# and pinging only *after* run_cycle() returns meant a long-but-healthy
# generation looked identical to a hang -- systemd SIGABRTed the service
# mid-generation (observed live on build 13G). Ping on a fixed interval well
# under WatchdogSec for the whole duration of a cycle instead.
WATCHDOG_PING_INTERVAL = 30


class WatchdogHeartbeat:
    """Pings systemd's watchdog on a fixed interval from a background thread.

    Started before run_cycle() and stopped once it returns (success or
    failure), so a long-running-but-alive cycle keeps the watchdog fed
    without any change to run_cycle() itself. Usable as a context manager.
    """

    def __init__(self, interval=None, notify_fn=None):
        self.interval = interval if interval is not None else WATCHDOG_PING_INTERVAL
        # Default resolved at start() time so tests (and the ImportError
        # fallback) see the current module-level notify.
        self.notify_fn = notify_fn
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        fn = self.notify_fn if self.notify_fn is not None else notify

        if fn is None:
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(fn,),
            name="watchdog-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def _run(self, fn):
        while not self._stop.is_set():
            try:
                fn("WATCHDOG=1")
            except Exception:
                # A failed ping must never kill the heartbeat loop; the
                # next interval gets another chance.
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

    global _pool, _monitor, _health_worker, _last_network_discovery

    # 2026-08-09: DeepSeek worker pool — primary AI engine per operator directive.
    _pool = start_pool(workers=8)

    # Telegram monitor — periodic status digests per operator directive.
    _monitor = TelegramMonitor()
    _monitor.start()

    # 2026-08-09: Health Worker — permanent health sampling thread
    # (Kai Mobile Command Node sub-project 4). Runs every 30s independently
    # of the orchestrator cycle.
    from core.health_worker import start_worker
    _health_worker = start_worker()

    info("scheduler started (DeepSeek pool + Telegram monitor + Health Worker)")

    if notify:
        notify("READY=1")


    while True:

        # TK-b7614289: Check pause flag before every cycle.
        # Pausing doesn't kill the running cycle — it just skips
        # future cycles until the flag is removed.
        if SCHEDULER_PAUSE_FILE.exists():
            try:
                pause_data = json.loads(SCHEDULER_PAUSE_FILE.read_text())
                if pause_data.get("paused", True):
                    # Log once per pause state, not every interval
                    paused_at = pause_data.get("paused_at", "unknown")
                    reason = pause_data.get("reason", "no reason given")
                    info(f"scheduler paused since {paused_at}: {reason}")
                    time.sleep(INTERVAL)
                    continue
            except Exception:
                # Corrupt pause file — log and remove
                info("scheduler pause file corrupt — removing and resuming")
                SCHEDULER_PAUSE_FILE.unlink(missing_ok=True)

        heartbeat = WatchdogHeartbeat()

        heartbeat.start()

        try:

            result = run_cycle()

            # Network discovery — runs every 300s, independent of the 60s cycle
            now = time.time()
            if now - _last_network_discovery >= _network_discovery_interval:
                _last_network_discovery = now
                run_network_discovery_cycle()

            findings = len(
                result.get("findings", [])
            )

            incidents = len(
                result.get("incidents", [])
            )

            decisions = len(
                result.get("decisions", [])
            )


            info(
                f"cycle completed findings={findings} incidents={incidents} decisions={decisions}"
            )

            Path("/var/lib/ai-orchestrator/heartbeat").write_text(
                datetime.now(timezone.utc).isoformat()
            )

            if notify:
                notify("WATCHDOG=1")


        except Exception as e:

            info(
                f"scheduler error: {str(e)}"
            )

        finally:

            heartbeat.stop()


        time.sleep(INTERVAL)



if __name__ == "__main__":

    start()
