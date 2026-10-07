import os
from concurrent.futures import ThreadPoolExecutor
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from dotenv import load_dotenv

_SESSION = requests.Session()
_SESSION.headers.update({"Connection": "keep-alive"})

def _par_map(fns):
    """Run {label: fn()} concurrently; return {label: result}. Kai fix 2026-10-06:
    PVE API calls are TLS-handshake-bound (~250ms each, 5-7 sequential in
    status/status_b => 1.5-4s). Parallel + keep-alive => ~300-900ms."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fn): label for label, fn in fns.items()}
        return {futures[fut]: fut.result() for fut in futures}


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

    # Add :8006 only when host has no explicit port (tunnel case already
    # carries its own port — e.g. socat 192.168.1.110:8009 -> pve-A:8006;
    # appending :8006 there produced host:8009:8006 and InvalidURL).
    url_host = host if ":" in host else f"{host}:8006"
    url = f"https://{url_host}/api2/json{path}"

    if token_id and token_secret:
        auth = f"PVEAPIToken={token_id}={token_secret}"
    else:
        auth = f"PVEAPIToken={token_secret}"

    headers = {"Authorization": auth}

    try:
        r = _SESSION.get(url, headers=headers, verify=_get_verify(), timeout=10)
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


def get_tasks(node=None, host=None, token_id=None, token_secret=None, limit=50, typefilter=None):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    query = f"limit={limit}"
    # Server-side filtering (e.g. typefilter=vzdump) is supported by the PVE
    # task API. The unfiltered recent window is dominated by high-frequency
    # tasks (push_file), so vzdump jobs scroll out of it entirely -- filtering
    # server-side is the only reliable way to ask "did a backup run?".
    if typefilter:
        query += f"&typefilter={typefilter}"
    return api_request(f"/nodes/{node}/tasks?{query}", host, token_id, token_secret)


def get_backup_content(storage=None, node=None, host=None, token_id=None, token_secret=None):
    """List backups on a backup-capable storage (``?content=backup``).

    The returned ``ctime`` is when the backup file was written -- an
    independent, durable signal that a backup exists, used as a fallback
    when the node task history has scrolled past the vzdump jobs.
    """
    node = node or os.getenv("PROXMOX_NODE", "pve")
    storage = storage or os.getenv("PROXMOX_BACKUP_STORAGE", "kai-c")
    return api_request(
        f"/nodes/{node}/storage/{storage}/content?content=backup",
        host, token_id, token_secret
    )


def get_network(node=None, host=None, token_id=None, token_secret=None):
    node = node or os.getenv("PROXMOX_NODE", "pve")
    return api_request(f"/nodes/{node}/network", host, token_id, token_secret)


def status():
    """Proxmox A status: socat 8009 forwards to A:8006; token belongs to A."""
    NODE = "pve-A"
    kw = {"host": None, "token_id": None, "token_secret": None}
    return _par_map({
        "node":    lambda: get_node_status(node=NODE),
        "lxc":     lambda: get_lxc(node=NODE),
        "qemu":    lambda: get_qemu(node=NODE),
        "tasks":   lambda: get_tasks(node=NODE),
        "network": lambda: get_network(node=NODE),
    })


# A vzdump job that has been running for longer than this is not evidence of
# a *completed* backup -- treat it as still-in-progress and don't count it.
VZDUMP_QUERY_LIMIT = 200


def status_b():
    """Proxmox B status via direct LAN (192.168.1.110:8006).

    Uses PROXMOX_B_TOKEN_ID + PROXMOX_B_TOKEN_SECRET (or falls back to
    PROXMOX_B_TOKEN env var) for authentication.
    """
    token_id = os.getenv("PROXMOX_B_TOKEN_ID", "")
    token_secret = os.getenv("PROXMOX_B_TOKEN_SECRET", os.getenv("PROXMOX_B_TOKEN", ""))
    # Endpoint: host may already carry its own port (e.g. socat tunnel
    # 192.168.1.110:8009 -> pve-A:8006); only append PROXMOX_B_PORT when the
    # host value has no port, otherwise we produced host:8009:8006 (InvalidURL).
    host = os.getenv("PROXMOX_B_HOST", "192.168.1.110")
    port = os.getenv("PROXMOX_B_PORT", "8006")
    endpoint = host if ":" in host else f"{host}:{port}"
    # Kai fix 2026-10-06: run the 7 PVE API calls concurrently (was sequential
    # 1.5s+; now ~max(individual) ≈ 300ms).
    fns = {
        "node":    lambda: get_node_status(host=endpoint, token_id=token_id, token_secret=token_secret),
        "lxc":     lambda: get_lxc(host=endpoint, token_id=token_id, token_secret=token_secret),
        "qemu":    lambda: get_qemu(host=endpoint, token_id=token_id, token_secret=token_secret),
        "tasks":   lambda: get_tasks(host=endpoint, token_id=token_id, token_secret=token_secret),
        "network": lambda: get_network(host=endpoint, token_id=token_id, token_secret=token_secret),
        # Backup-specific signals (2026-09-20): the unfiltered recent task
        # window is dominated by push_file and misses vzdump jobs entirely.
        # Query the filtered task list AND the backup storage directly so the
        # health check can prove "backups are running" instead of guessing
        # from a window that scrolls.
        "backup_tasks": lambda: get_tasks(
            host=endpoint, token_id=token_id, token_secret=token_secret,
            limit=VZDUMP_QUERY_LIMIT, typefilter="vzdump",
        ),
        "backup_content": lambda: get_backup_content(
            host=endpoint, token_id=token_id, token_secret=token_secret,
        ),
    }
    return _par_map(fns)


if __name__ == "__main__":
    print(status())
