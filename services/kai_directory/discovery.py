from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional

from .models import Record, slugify

LOOPBACK_BIND = ("127.0.0.1", "::1", "localhost")
NOISE_PORTS = {22, 25, 111, 631, 5432, 6379, 9090, 2019, 20241, 20242}


def parse_registry(doc: Optional[dict], host_label: str = "") -> list[Record]:
    out: list[Record] = []
    for s in (doc or {}).get("services", []):
        name = s.get("name") or s.get("id")
        if not name:
            continue
        out.append(Record(
            id=slugify(name), name=slugify(name), display_name=name,
            category=s.get("category", "general"),
            host=s.get("host", host_label), ip=s.get("host", ""),
            port=int(s.get("port") or 0), bind=s.get("bind", "0.0.0.0"),
            owner=s.get("owner", ""), tags=s.get("tags", ""), source="registry",
        ))
    return out


_SS_RE = re.compile(r"LISTEN\s+\d+\s+\d+\s+(\S+):(\d+)\s")


def parse_ss(output: str, ip: str, host: str = "") -> list[Record]:
    out: list[Record] = []
    for line in output.splitlines():
        m = _SS_RE.search(line)
        if not m:
            continue
        bind = m.group(1).strip("[]")
        if bind in LOOPBACK_BIND:
            continue
        port = int(m.group(2))
        if port in NOISE_PORTS:
            continue
        proc = ""
        pm = re.search(r'users:\(\("([^"]+)"', line)
        if pm:
            proc = pm.group(1)
        name = slugify(f"{proc}-{port}" if proc else f"{host}-{port}")
        out.append(Record(id=name, name=name, display_name=name, category="infra",
                          host=host, ip=ip, port=port, bind=bind, source="ss",
                          tags=proc))
    # dedupe by (ip,port)
    seen, dedup = set(), []
    for r in out:
        key = (r.ip, r.port)
        if key not in seen:
            seen.add(key); dedup.append(r)
    return dedup
