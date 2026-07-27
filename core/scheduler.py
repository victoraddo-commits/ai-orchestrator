import time
from pathlib import Path
from datetime import datetime, timezone

try:
    from systemd.daemon import notify
except ImportError:
    notify = None

from core.orchestrator_cycle import run_cycle
from core.logger import info


INTERVAL = 300


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
