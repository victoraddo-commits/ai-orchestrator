from __future__ import annotations

import json

from .models import Record

SUFFIX = "tail82a9ca.ts.net"


def assign_names(records: list[Record], proxy_for: dict[str, str]) -> list[Record]:
    used: set[str] = set()
    for r in records:
        base = r.name or r.id
        name, i = base, 1
        while name in used:
            i += 1
            name = f"{base}-{i}"
        used.add(name)
        r.name = name
        r.tailnet_name = f"{name}.{SUFFIX}"
        r.internal_url = f"https://{name}.{SUFFIX}/"
        r.proxy_node = proxy_for.get(r.id, r.proxy_node)
    return records


def policy_block(records: list[Record]) -> str:
    services = {}
    for r in records:
        if r.tailnet_name:
            services[f"svc:{r.name}"] = {"endpoints": ["tcp:443"]}
    return json.dumps({"services": services}, indent=2)


def serve_commands(records: list[Record], node: str) -> list[str]:
    return [
        f"tailscale serve --bg --service=svc:{r.name} {r.target_url}"
        for r in records
        if r.proxy_node == node and r.target_url
    ]
