from core.scanner import scan
from core.logger import info, error


def run():

    info("orchestrator scan started")

    try:

        result = scan()

        info(
            f"scan completed for {result['hostname']}"
        )

        return result

    except Exception as e:

        error(
            f"scan failed: {str(e)}"
        )

        return {
            "error": str(e)
        }


if __name__ == "__main__":

    print(run())
