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


# Was 300s, tuned for infra health checks (Phase 1-11) where that cadence is
# fine. The build/roadmap pipeline (Phase 12C/12L) inherited this same cycle
# and 5-minute waits between build-progression steps were consistently the
# biggest source of "why is nothing happening" during live testing. Cheap
# reads (Proxmox/Docker checks) at 5x the frequency cost effectively nothing.
INTERVAL = 60


def start():

    info("scheduler started")

    if notify:
        notify("READY=1")


    while True:

        try:

            result = run_cycle()

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


        time.sleep(INTERVAL)



if __name__ == "__main__":

    start()
