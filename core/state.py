from datetime import datetime
from core.memory import save


def build_state(
    docker=None,
    host=None,
    services=None,
    proxmox=None
):

    state = {

        "timestamp": datetime.now().isoformat(),

        "host": host or {},

        "docker": docker or {},

        "services": services or {},

        "proxmox": proxmox or {}

    }


    save(
        "system_state.json",
        state
    )


    return state



if __name__ == "__main__":

    print(
        build_state()
    )
