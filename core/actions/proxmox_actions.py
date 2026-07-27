import os
import requests


def restart_lxc(service):

    host = os.getenv("PROXMOX_HOST")
    token = os.getenv("PROXMOX_TOKEN")
    node = os.getenv("PROXMOX_NODE")

    if not all([host, token, node]):
        return {
            "status": "failed",
            "reason": "missing_proxmox_environment"
        }


    url = f"https://{host}:8006/api2/json/nodes/{node}/lxc/{service}/status/restart"


    headers = {
        "Authorization": f"PVEAPIToken={token}"
    }


    try:

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
                "container": service
            }


        return {
            "status": "failed",
            "reason": response.text
        }


    except Exception as e:

        return {
            "status": "failed",
            "reason": str(e)
        }
