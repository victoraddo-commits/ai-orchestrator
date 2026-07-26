from core.scanner import scan
from core.state import build_state


def refresh_state():

    result = scan()

    build_state(
        docker=result.get("docker"),
        host={
            "hostname": result.get("hostname")
        }
    )

    return result


if __name__ == "__main__":

    print(refresh_state())
