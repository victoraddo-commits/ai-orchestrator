"""Network-aware Proxmox discovery — extends proxmox_registry with routing data.

Correlates Tailscale IP ↔ Proxmox node ↔ LAN IP ↔ subnets.
Uses existing proxmox_monitor.PROXMOX_NODES and SSH commands (no API calls).
"""

import os, re, json, subprocess
from datetime import datetime, timezone
from typing import Optional

from core.proxmox_monitor import _get_node_configs


# -------------------------------------------------------------------
# SSH helpers (same pattern as tailscale_discovery)
# -------------------------------------------------------------------

def _default_ssh_key() -> str:
    """First existing SSH key usable for node discovery.

    The live runner (LXC 111) authenticates to Proxmox B with
    ``/root/.ssh/kai_pve_usage`` (the same key the usage collector uses); it has
    no ``id_rsa``. Resolve at call time so tests can inject their own key.
    """
    for cand in (os.environ.get("KAI_DISCOVERY_SSH_KEY"),
                 os.environ.get("PROXMOX_SSH_KEY"),
                 "/root/.ssh/kai_pve_usage",
                 "/root/.ssh/id_rsa"):
        if cand and os.path.exists(cand):
            return cand
    return os.environ.get("PROXMOX_SSH_KEY", "/root/.ssh/id_rsa")


def build_ssh_command(node: dict, cmd: str) -> list:
    """Build the ``ssh`` argv for one discovery command on ``node``.

    SITE-A (Proxmox A) is only reachable from the runner through Proxmox B, so
    such nodes set ``proxy_jump``: the jump authenticates with ``proxy_key``
    (defaulting to the node key) and forwards the channel with ``-W %h:%p``.
    The same authorised key then authenticates the final hop. ``BatchMode=yes``
    is always set so discovery never blocks on an interactive prompt.
    """
    key = node.get("ssh_key") or _default_ssh_key()
    full_cmd = [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=6",
    ]
    jump = node.get("proxy_jump")
    if jump:
        proxy_key = node.get("proxy_key") or key
        proxy = (f"ssh -i {proxy_key} -o StrictHostKeyChecking=no "
                 f"-o BatchMode=yes -o ConnectTimeout=6 -W %h:%p {jump}")
        full_cmd += ["-o", f"ProxyCommand={proxy}"]
    full_cmd += [f"root@{node['host']}", cmd]
    return full_cmd


def _ssh(node: dict, cmd: str) -> tuple[str, str, int]:
    full_cmd = build_ssh_command(node, cmd)
    try:
        r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 124


# -------------------------------------------------------------------
# NIC inventory (link state, speed, MAC, counters, bridge/VLAN mapping)
# -------------------------------------------------------------------

_PHYSICAL_IFACE_RE = re.compile(r"^(nic|eth|en|em|eno|ens|enp|bond|wl)")
_VLAN_RE = re.compile(r"^.+\.[0-9]+$")
_VIRTUAL_IFACE_RE = re.compile(r"^(tap|veth|fwln|fwpr|vnet)")

_NIC_SCRIPT = r"""
set -u
echo '===ADDR==='
timeout 6 ip -j addr show 2>/dev/null || true
echo '===LINK==='
timeout 6 ip -j link show 2>/dev/null || true
echo '===SYSFS==='
for i in /sys/class/net/*; do
  n=$(basename "$i")
  printf '%s|%s|%s|%s|%s|%s|%s|%s\n' "$n" \
    "$(cat "$i/operstate" 2>/dev/null)" \
    "$(cat "$i/speed" 2>/dev/null)" \
    "$(cat "$i/statistics/rx_bytes" 2>/dev/null)" \
    "$(cat "$i/statistics/tx_bytes" 2>/dev/null)" \
    "$(cat "$i/statistics/rx_errors" 2>/dev/null)" \
    "$(cat "$i/statistics/tx_errors" 2>/dev/null)" \
    "$(cat "$i/carrier" 2>/dev/null)"
done
echo '===BRIDGE==='
ls -d /sys/class/net/*/bridge 2>/dev/null | sed 's#/sys/class/net/##; s#/bridge##' || true
"""


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("===") and line.endswith("==="):
            if cur is not None:
                sections[cur] = "\n".join(buf)
            cur = line.strip("=")
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf)
    return sections


def _int_or_none(value: str) -> Optional[int]:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _classify_iface(name: str, bridges: set[str]) -> str:
    if name in bridges or name.startswith(("vmbr", "br")):
        return "bridge"
    if name.startswith(("tailscale", "zt", "wg")):
        return "vpn"
    if _VIRTUAL_IFACE_RE.match(name):
        return "virtual"
    if _PHYSICAL_IFACE_RE.match(name):
        return "physical"
    return "other"


def parse_nic_inventory(text: str) -> dict:
    """Parse the collector output into a rich NIC/bridge/VLAN inventory.

    Pure function so it can be unit-tested with captured ``ip -j`` + sysfs
    fixtures. Never invents values: missing fields are ``None``/``0``.
    """
    sections = _split_sections(text)
    try:
        addr = json.loads(sections.get("ADDR") or "[]") or []
    except json.JSONDecodeError:
        addr = []
    try:
        link = json.loads(sections.get("LINK") or "[]") or []
    except json.JSONDecodeError:
        link = []

    sysfs: dict[str, dict] = {}
    for line in (sections.get("SYSFS") or "").splitlines():
        parts = line.split("|")
        if len(parts) < 8 or not parts[0].strip():
            continue
        sysfs[parts[0].strip()] = {
            "operstate": parts[1].strip() or None,
            "speed_mbps": _int_or_none(parts[2]),
            "rx_bytes": _int_or_none(parts[3]) or 0,
            "tx_bytes": _int_or_none(parts[4]) or 0,
            "rx_errors": _int_or_none(parts[5]) or 0,
            "tx_errors": _int_or_none(parts[6]) or 0,
            "carrier": parts[7].strip() not in ("", "0"),
        }
    bridges = {b.strip() for b in (sections.get("BRIDGE") or "").split() if b.strip()}

    link_by = {l.get("ifname"): l for l in link if l.get("ifname")}
    addr_by = {a.get("ifname"): a for a in addr if a.get("ifname")}

    nics: list[dict] = []
    # Iterate real interfaces only (``ip`` sees them); sysfs alone can contain
    # non-interface entries such as ``bonding_masters``.
    for name in sorted(set(addr_by) | set(link_by)):
        if name == "lo":
            continue
        l = link_by.get(name, {})
        a = addr_by.get(name, {})
        s = sysfs.get(name, {})
        ips = [ai.get("local") for ai in (a.get("addr_info") or [])
               if ai.get("family") == "inet" and ai.get("local")]
        flags = l.get("flags") or []
        operstate = (s.get("operstate") or l.get("operstate") or "unknown").lower()
        up = operstate == "up" or "UP" in flags
        kind = _classify_iface(name, bridges)
        nics.append({
            "name": name,
            "kind": kind,
            "operstate": operstate,
            "up": up,
            "speed_mbps": s.get("speed_mbps"),
            "mac": a.get("address") or l.get("address"),
            "mtu": l.get("mtu"),
            "master": l.get("master"),
            "ip": ips[0] if ips else None,
            "ips": ips,
            "altnames": a.get("altnames") or [],
            "rx_bytes": s.get("rx_bytes", 0),
            "tx_bytes": s.get("tx_bytes", 0),
            "rx_errors": s.get("rx_errors", 0),
            "tx_errors": s.get("tx_errors", 0),
            "carrier": bool(s.get("carrier")),
        })

    vlans = [n["name"] for n in nics if _VLAN_RE.match(n["name"])]
    available_wan = sorted(
        n["name"] for n in nics
        if n["kind"] == "physical" and not n["up"] and not n["master"])
    return {
        "nics": nics,
        "bridges": sorted(bridges),
        "vlans": vlans,
        "available_wan": available_wan,
    }


def collect_nic_inventory(node: dict) -> dict:
    """Run the NIC collector on one node and parse it (never raises)."""
    empty = {"nics": [], "bridges": [], "vlans": [], "available_wan": []}
    stdout, stderr, rc = _ssh(node, _NIC_SCRIPT)
    if rc != 0 and not (stdout or "").strip():
        return {**empty, "error": (stderr or f"exit {rc}").strip()}
    try:
        return parse_nic_inventory(stdout)
    except Exception as e:  # noqa: BLE001
        return {**empty, "error": f"{type(e).__name__}: {e}"}


# -------------------------------------------------------------------
# Per-node network discovery
# -------------------------------------------------------------------

def discover_node_networking(node: dict) -> dict:
    """Gather networking data for one Proxmox node via SSH."""
    result = {
        "node": node["name"],
        "reachable": False,
        "interfaces": [],
        "bridges": [],
        "vlans": [],
        "routing_table": [],
        "tailscale_ip": None,
        "lan_ip": None,
        "gateway": None,
    }

    # Track per-command success for reachable check
    ssh_ok = False

    # ip addr show
    stdout, _, rc = _ssh(node, "ip -j addr show")
    if rc == 0:
        ssh_ok = True
        try:
            for iface in json.loads(stdout):
                ipv4 = [ai.get("local") for ai in (iface.get("addr_info") or [])
                        if ai.get("family") == "inet" and ai.get("local")]
                info = {"name": iface.get("ifname"), "ip": ipv4[0] if ipv4 else None,
                        "ips": ipv4, "mac": iface.get("address")}
                result["interfaces"].append(info)
            # Identify the LAN IP: first IPv4 on a LAN-ish interface (not
            # loopback / tailnet / zerotier / docker).
            for info in result["interfaces"]:
                name = info["name"] or ""
                if name.startswith(("lo", "tailscale", "zt", "docker", "br-", "veth", "tap")):
                    continue
                for ip in info.get("ips") or []:
                    if not ip.startswith("127."):
                        result["lan_ip"] = ip
                        break
                if result["lan_ip"]:
                    break
        except json.JSONDecodeError:
            # JSON parse failed — interface data unavailable, continue without it
            pass

    # ip route show
    stdout, _, rc = _ssh(node, "ip -j route show")
    if rc == 0:
        ssh_ok = True
        try:
            for route in json.loads(stdout):
                result["routing_table"].append({
                    "dst": route.get("dst", ""),
                    "gateway": route.get("gateway"),
                    "dev": route.get("dev"),
                    "table": route.get("table"),
                })
        except json.JSONDecodeError:
            # JSON parse failed — routing table unavailable, continue without it
            pass

    # Tailscale IP detection
    stdout, _, rc = _ssh(node, "tailscale ip -4")
    if rc == 0:
        ssh_ok = True
        result["tailscale_ip"] = stdout.strip()

    # Default gateway
    stdout, _, rc = _ssh(node, "ip route show default")
    if rc == 0:
        ssh_ok = True
        parts = stdout.split()
        if "via" in parts:
            idx = parts.index("via")
            result["gateway"] = parts[idx + 1] if idx + 1 < len(parts) else None

    result["reachable"] = ssh_ok

    # Rich NIC inventory (link state, speed, MAC, counters, bridge/VLAN map).
    nic_inv = collect_nic_inventory(node)
    result["nics"] = nic_inv.get("nics", [])
    result["bridges"] = nic_inv.get("bridges", [])
    result["vlans"] = nic_inv.get("vlans", [])
    result["available_wan"] = nic_inv.get("available_wan", [])
    if nic_inv.get("nics"):
        ssh_ok = True
    if nic_inv.get("error"):
        result["nic_error"] = nic_inv["error"]
    result["reachable"] = ssh_ok
    return result


# -------------------------------------------------------------------
# Correlation
# -------------------------------------------------------------------

def _correlate_tailscale_to_node(ts_data: dict, px_nodes: dict) -> dict:
    """Match Tailscale peer IPs to Proxmox node configs.

    Builds a lookup dict keyed by tailscale_ip (O(m) instead of O(n×m)),
    then annotates matching px_nodes in-place and returns the same dict.
    """
    # Build tailscale_ip → {node_name, role} lookup
    ts_ip_map: dict[str, dict] = {}
    for node_name, ts_peer in ts_data.items():
        for peer_name, peer_info in (ts_peer.get("peers") or {}).items():
            ts_ip = peer_info.get("tailscale_ip")
            if ts_ip:
                ts_ip_map[ts_ip] = {"tailscale_peer": node_name, "role": peer_info.get("role")}

    # Annotate px_nodes in-place
    for px_name, px_node in px_nodes.items():
        match = ts_ip_map.get(px_node.get("tailscale_ip"))
        if match:
            px_nodes[px_name]["tailscale_peer"] = match["tailscale_peer"]
            px_nodes[px_name]["role"] = match["role"]

    return px_nodes


def discover_all_nodes() -> dict:
    """Full network-aware Proxmox discovery across all configured nodes.

    Hosts are corrected to the verified live estate: Proxmox B (the reachable
    node that hosts LXC 100-114) is ``192.168.1.110``. Proxmox A is reached via
    a ProxyCommand jump through Proxmox B on the tailnet address, using the key
    that is authorised on both. Env overrides win when present.
    """
    overrides = {
        # SITE-A (Proxmox A, node "pve"): the runner cannot route to its LAN
        # address, so dial its tailnet IP through Proxmox B.
        "pve": {
            "host": os.environ.get("KAI_NETWORK_PVE_A_HOST", "100.83.4.27"),
            "proxy_jump": os.environ.get(
                "KAI_NETWORK_PVE_A_JUMP",
                os.environ.get("KAI_NETWORK_PVE_B_HOST", "192.168.1.110")),
        },
        "pve-b": {
            "host": os.environ.get("KAI_NETWORK_PVE_B_HOST", "192.168.1.110"),
        },
    }
    results = {}
    for node in _get_node_configs():
        node = dict(node)
        override = overrides.get(node["name"])
        if override:
            node.update({k: v for k, v in override.items() if v})
        results[node["name"]] = discover_node_networking(node)
    return results
