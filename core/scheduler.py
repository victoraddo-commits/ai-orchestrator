import time
from core.engine import run
from core.logger import info


INTERVAL = 300


def start():

    info("scheduler started")


    while True:

        try:

            result = run()

            findings = len(
                result.get("findings", [])
            )

            info(
                f"cycle completed findings={findings}"
            )


        except Exception as e:

            info(
                f"scheduler error: {str(e)}"
            )


        time.sleep(INTERVAL)



if __name__ == "__main__":

    start()
