import time

from core.orchestrator_cycle import run_cycle
from core.logger import info


INTERVAL = 300


def start():

    info("scheduler started")


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


        except Exception as e:

            info(
                f"scheduler error: {str(e)}"
            )


        time.sleep(INTERVAL)



if __name__ == "__main__":

    start()
