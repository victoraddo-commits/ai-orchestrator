from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Optional

from .models import Record, slugify

LOOPBACK_BIND = ("127.0.0.1", "::1", "localhost")
NOISE_PORTS = {22, 25, 111, 631, 5432, 6379, 9090, 2019, 20241, 20242}


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_registry(doc: Optional[dict], host_label: str = "") -> list[Record]:
    out: list[Record] = []
    for s in (doc or {}).get("services", []):
        name = s.get("name") or s.get("id")
        if not name:
            continue
        h = s.get("host") or host_label
        out.append(Record(
            id=slugify(name), name=slugify(name), display_name=name,
            category=s.get("category", "general"),
            host=h, ip=h,
            port=_to_int(s.get("port")), bind=s.get("bind", "0.0.0.0"),
            owner=s.get("owner", ""), tags=s.get("tags", ""), source="registry",
        ))
    return out


_SS_RE = re.compile(r"LISTEN\s+\d+\s+\d+\s+(\S+):(\d+)(?:\s|$)")


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


def parse_docker(output: str, ip: str, host: str = "") -> list[Record]:
    out: list[Record] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        cname, ports, image = parts[0], parts[1], parts[2]
        pm = re.search(r"0\.0\.0\.0:(\d+)->", ports)
        if not pm:
            continue
        short = cname.replace("kai-money-", "").removesuffix("-1")
        name = slugify(short)
        out.append(Record(id=name, name=name, display_name=cname, category="infra",
                          host=host, container=f"docker:{cname}", ip=ip,
                          port=int(pm.group(1)), bind="0.0.0.0", source="docker",
                          tags=image))
    return out


_CF_RE = re.compile(r"-\s*hostname:\s*(\S+)\s*\n\s*service:\s*(\S+)")


def parse_cloudflared(yml: str) -> list[Record]:
    out = []
    for hostname, service in _CF_RE.findall(yml or ""):
        name = slugify(hostname.split(".")[0])
        out.append(Record(id=name, name=name, display_name=hostname,
                          category="web", source="cloudflared", tags=service))
    return out


_PANEL_RE = re.compile(r'data-hash="#([a-z0-9-]+)"')


def parse_panels(html_text: str) -> list[Record]:
    out = []
    for name in sorted(set(_PANEL_RE.findall(html_text or ""))):
        out.append(Record(id=f"panel-{name}", name=slugify(name),
                          display_name=f"CC panel: {name}", category="ui",
                          source="panel"))
    return out


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _read_text(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()
    except Exception:
        return ""


REGISTRY_PATHS = [
    os.environ.get("KAI_SERVICES_JSON", "/var/lib/kai-directory/kai_services.json"),
]
CLOUDFLARED_DIRS = ["/etc/cloudflared", "/root/.cloudflared"]
PANEL_PATH = os.environ.get("KAI_CC_HTML", "/var/lib/kai-directory/command_center.html")
SELF_IP = os.environ.get("KAI_DIRECTORY_SELF_IP", "192.168.1.114")


def discover_all() -> list[Record]:
    """Collect from all sources. Any source failing yields no records (never raises)."""
    recs: list[Record] = []

    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return []

    for p in REGISTRY_PATHS:
        recs += _safe(parse_registry, _read_json(p))
    recs += _safe(parse_ss, _run(["ss", "-ltn"]), ip=SELF_IP, host="ct114")
    docker_out = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}\t{{.Image}}"])
    if docker_out:
        recs += _safe(parse_docker, docker_out, ip=SELF_IP, host="ct114")
    for d in CLOUDFLARED_DIRS:
        try:
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if fn.endswith((".yml", ".yaml")):
                    recs += _safe(parse_cloudflared, _read_text(os.path.join(d, fn)))
        except Exception:
            continue
    recs += _safe(parse_panels, _read_text(PANEL_PATH))
    # dedupe by id (first source wins)
    seen, dedup = set(), []
    for r in recs:
        if r.id not in seen:
            seen.add(r.id); dedup.append(r)
    return dedup
