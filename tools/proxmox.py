import os
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from dotenv import load_dotenv

load_dotenv()


def _get_verify():
    """Return the CA cert path for TLS verification, or False to disable it."""
    ca_cert = os.getenv("PROXMOX_CA_CERT", "")
    return ca_cert if ca_cert else False


def api_request(path, host=None, token_id=None, token_secret=None):
    """Make a Proxmox API request.

    Uses env defaults (PROXMOX_HOST, PROXMOX_TOKEN_ID, PROXMOX_TOKEN_SECRET)
    unless overridden per-call for multi-node setups.
    """
    host = host or os.getenv("PROXMOX_HOST", "localhost")
    token_id = token_id or os.getenv("PROXMOX_TOKEN_ID", "")
    token_secret = token_secret or os.getenv("PROXMOX_TOKEN_SECRET", os.getenv("PROXMOX_TOKEN", ""))

    if not host:
        return {"error": "Missing PROXMOX_HOST"}
    if not token_id and not token_secret:
        return {"error": "Missing Proxmox API token"}

    # Add :8006 only when host has no explicit port (tunnel case already carries its own port)
    if ":" not in host:
        url_host = f"{host}:8006"
    else:
        url_host = host
    url = f"https://{url_host}/api2/json{path}"

    if token_id and token_secret:
        auth = f"PVEAPIToken={token_id}={token_secret}"
    else:
        auth = f"PVEAPIToken={token_secret}"

    headers = {"Authorization": auth}

    try:
        r = requests.get(url, headers=headers, verify=_get_verify(), timeout=10)
    except Exception as e:
        # Transport-level failure: DNS, refused connection, TLS, timeout.
        return {"error": "unreachable", "detail": str(e)}

    # Non-2xx responses (401/403 expired-or-missing token, 500, ...) usually
    # carry a non-JSON body. Calling r.json() on them used to raise
    # "Expecting value: line 1 column 1" which callers misread as the node
    # being unreachable. Report the real shape honestly instead.
    if r.status_code in (401, 403):
        return {"error": "auth_failed", "http": r.status_code}

    if not (200 <= r.status_code < 300):
        return {
            "error": "http_error",
            "http": r.status_code,
            "detail": (r.text or "")[:200],
        }

    try:
        return r.json()
    except ValueError:
        return {
            "error": "invalid_json",
            "http": r.status_code,
            "detail": (r.text or "")[:200],
        }


def get_node_status(node=None, host=None, token_id=None, token_secret=None):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    return api_request(f"/nodes/{node}/status", host, token_id, token_secret)


def get_lxc(node=None, host=None, token_id=None, token_secret=None):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    return api_request(f"/nodes/{node}/lxc", host, token_id, token_secret)


def get_qemu(node=None, host=None, token_id=None, token_secret=None):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    return api_request(f"/nodes/{node}/qemu", host, token_id, token_secret)


def get_tasks(node=None, host=None, token_id=None, token_secret=None, limit=50):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    return api_request(f"/nodes/{node}/tasks?limit={limit}", host, token_id, token_secret)


def get_network(node=None, host=None, token_id=None, token_secret=None):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    return api_request(f"/nodes/{node}/network", host, token_id, token_secret)


def status():
    """Default status using env vars (Proxmox A)."""
    return {
        "node": get_node_status(),
        "lxc": get_lxc(),
        "qemu": get_qemu(),
        "tasks": get_tasks(),
        "network": get_network()
    }


def status_b():
    """Proxmox B status via direct LAN (192.168.1.109:8006).

    Uses PROXMOX_B_TOKEN_ID + PROXMOX_B_TOKEN_SECRET (or falls back to
    PROXMOX_B_TOKEN env var) for authentication.
    """
    token_id = os.getenv("PROXMOX_B_TOKEN_ID", "")
    token_secret = os.getenv("PROXMOX_B_TOKEN_SECRET", os.getenv("PROXMOX_B_TOKEN", ""))
    # Direct LAN — no SSH tunnel needed
    host = os.getenv("PROXMOX_B_HOST", "192.168.1.110")
    port = os.getenv("PROXMOX_B_PORT", "8006")
    endpoint = f"{host}:{port}"
    return {
        "node": get_node_status(host=endpoint, token_id=token_id, token_secret=token_secret),
        "lxc": get_lxc(host=endpoint, token_id=token_id, token_secret=token_secret),
        "qemu": get_qemu(host=endpoint, token_id=token_id, token_secret=token_secret),
        "tasks": get_tasks(host=endpoint, token_id=token_id, token_secret=token_secret),
        "network": get_network(host=endpoint, token_id=token_id, token_secret=token_secret)
    }


if __name__ == "__main__":
    print(status())
