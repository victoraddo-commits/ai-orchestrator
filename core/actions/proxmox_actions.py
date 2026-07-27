import os
import requests
from dotenv import load_dotenv

load_dotenv()


def find_lxc_id(service):

    host = os.getenv("PROXMOX_HOST")
    token = os.getenv("PROXMOX_TOKEN")
    node = os.getenv("PROXMOX_NODE")

    url = f"https://{host}:8006/api2/json/nodes/{node}/lxc"

    headers = {
        "Authorization": f"PVEAPIToken={token}"
    }

    r = requests.get(
        url,
        headers=headers,
        verify=False,
        timeout=10
    )

    data = r.json().get("data", [])

    for container in data:
        if (
            container.get("name") == service
            or container.get("vmid") == service
        ):
            return container.get("vmid")

    return None


def restart_lxc(service):

    host = os.getenv("PROXMOX_HOST")
    token = os.getenv("PROXMOX_TOKEN")
    node = os.getenv("PROXMOX_NODE")

    vmid = find_lxc_id(service)

    if not vmid:
        return {
            "status": "failed",
            "reason": f"lxc_not_found:{service}"
        }


    url = (
        f"https://{host}:8006/api2/json/"
        f"nodes/{node}/lxc/{vmid}/status/restart"
    )


    headers = {
        "Authorization": f"PVEAPIToken={token}"
    }


    response = requests.post(
        url,
        headers=headers,
        verify=False,
        timeout=10
    )


    if response.status_code == 200:

        return {
            "status": "success",
            "action": "restart_container",
            "container": service,
            "vmid": vmid
        }


    return {
        "status": "failed",
        "code": response.status_code,
        "reason": response.text
    }
