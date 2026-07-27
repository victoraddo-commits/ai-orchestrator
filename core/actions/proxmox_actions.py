import os
import requests
from dotenv import load_dotenv

load_dotenv()


def proxmox_request(method, url):
    token = os.getenv("PROXMOX_TOKEN")

    headers = {
        "Authorization": f"PVEAPIToken={token}"
    }

    return requests.request(
        method,
        url,
        headers=headers,
        verify=True,
        timeout=10
    )


def proxmox_url(path):
    host = os.getenv("PROXMOX_HOST")
    return f"https://{host}:8006/api2/json{path}"


def find_lxc_id(service):

    node = os.getenv("PROXMOX_NODE")

    url = proxmox_url(
        f"/nodes/{node}/lxc"
    )

    response = proxmox_request(
        "GET",
        url
    )

    if response.status_code != 200:
        return None

    containers = response.json()["data"]

    for c in containers:
        if c.get("name") == service:
            return c.get("vmid")

    return None


def lxc_action(service, action):

    node = os.getenv("PROXMOX_NODE")

    vmid = find_lxc_id(service)

    if not vmid:
        return {
            "success": False,
            "error": "Container not found"
        }

    url = proxmox_url(
        f"/nodes/{node}/lxc/{vmid}/status/{action}"
    )

    response = proxmox_request(
        "POST",
        url
    )

    return {
        "success": response.status_code == 200,
        "status_code": response.status_code,
        "response": response.json()
    }


def restart_lxc(service):
    return lxc_action(service, "reboot")


def start_lxc(service):
    return lxc_action(service, "start")


def stop_lxc(service):
    return lxc_action(service, "stop")


def get_lxc_status(service):

    node = os.getenv("PROXMOX_NODE")

    vmid = find_lxc_id(service)

    if not vmid:
        return None

    url = proxmox_url(
        f"/nodes/{node}/lxc/{vmid}/status/current"
    )

    response = proxmox_request(
        "GET",
        url
    )

    return response.json()
