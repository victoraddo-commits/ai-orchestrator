from __future__ import annotations

import json

from .models import Record

SUFFIX = "tail82a9ca.ts.net"


def assign_names(records: list[Record], proxy_for: dict[str, str]) -> list[Record]:
    used: set[str] = set()
    for r in records:
        r.proxy_node = proxy_for.get(r.id, r.proxy_node)
        if not (r.target_url or (r.ip and r.port)):
            r.tailnet_name = ""
            r.internal_url = ""
            continue
        if "ui-page" in (r.tags or ""):
            r.tailnet_name = ""
            r.internal_url = r.target_url
            continue
        base = r.name or r.id
        name, i = base, 1
        while name in used:
            i += 1
            name = f"{base}-{i}"
        used.add(name)
        r.name = name
        r.tailnet_name = f"{name}.{SUFFIX}"
        r.internal_url = f"https://{name}.{SUFFIX}/"
    return records


def proxy_node_for(ip: str) -> str:
    """PVE-A services live on the 192.168.99.0/24 fabric; PVE-B on 192.168.1.0/24."""
    ip = ip or ""
    if ip.startswith("192.168.99."):
        return "proxmox-a"
    if ip.startswith("192.168.1."):
        return "proxmox-b"
    return ""


def policy_block(records: list[Record]) -> str:
    services = {}
    for r in records:
        if r.tailnet_name:
            services[f"svc:{r.name}"] = {"endpoints": ["tcp:443"]}
    return json.dumps({"services": services}, indent=2)


def serve_commands(records: list[Record], node: str) -> list[str]:
    return [
        f"tailscale serve --service=svc:{r.name} --https=443 {r.target_url}"
        for r in records
        if r.proxy_node == node and r.target_url and r.tailnet_name
    ]
