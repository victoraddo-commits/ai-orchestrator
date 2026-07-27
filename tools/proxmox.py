import os
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from dotenv import load_dotenv

load_dotenv()


def api_request(path):

    host = os.getenv("PROXMOX_HOST")
    token = os.getenv("PROXMOX_TOKEN")

    if not host or not token:
        return {
            "error": "Missing Proxmox credentials"
        }

    url = f"https://{host}:8006/api2/json{path}"

    headers = {
        "Authorization": f"PVEAPIToken={token}"
    }

    try:

        r = requests.get(
            url,
            headers=headers,
            verify=False,
            timeout=10
        )

        return r.json()

    except Exception as e:

        return {
            "error": str(e)
        }


def get_node_status():

    node = os.getenv("PROXMOX_NODE")

    return api_request(
        f"/nodes/{node}/status"
    )


def get_lxc():

    node = os.getenv("PROXMOX_NODE")

    return api_request(
        f"/nodes/{node}/lxc"
    )


def get_qemu():

    node = os.getenv("PROXMOX_NODE")

    return api_request(
        f"/nodes/{node}/qemu"
    )


def get_tasks(limit=50):

    node = os.getenv("PROXMOX_NODE")

    return api_request(
        f"/nodes/{node}/tasks?limit={limit}"
    )


def get_network():

    node = os.getenv("PROXMOX_NODE")

    return api_request(
        f"/nodes/{node}/network"
    )


def status():

    return {
        "node": get_node_status(),
        "lxc": get_lxc(),
        "qemu": get_qemu(),
        "tasks": get_tasks(),
        "network": get_network()
    }


if __name__ == "__main__":
    print(status())
