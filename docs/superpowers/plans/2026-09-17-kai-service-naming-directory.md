# Kai Service Naming + Directory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Kai app/module a stable private Tailscale name and a single standalone directory service that lists them all, with a conformance check that proves nothing is missing.

**Architecture:** A standalone FastAPI + SQLite app (`kai-directory`) auto-discovers services from six sources (registry JSON, `ss` listeners, `docker ps`, cloudflared ingress, CC panel list, manual), normalises them into records, assigns `*.tail82a9ca.ts.net` names, checks health, and serves an API + HTML index + a Command Center panel. Names are realised with Tailscale Services (`tailscale serve --service=...`) on PVE-B/PVE-A proxy nodes.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, SQLite (stdlib `sqlite3`), pytest, systemd, Tailscale 1.102.

**Source of truth:** code lives in the orchestrator repo at `services/kai_directory/` (CT111), deployed to a new LXC `kai-directory` on PVE-B.

**Deploy access (used by commands below):** from LXC113:
```bash
PVEB='ssh -i /root/.ssh/pve2_deploy -o BatchMode=yes -o StrictHostKeyChecking=no root@192.168.1.110'
# run inside CT111:
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && <cmd>"'
# run inside the new directory LXC (VMID 114):
$PVEB 'pct exec 114 -- sh -c "<cmd>"'
```

---

## File Structure

```
services/kai_directory/
  __init__.py            # package marker
  models.py              # slugify, Record dataclass, normalisation helpers
  store.py               # RecordStore: SQLite CRUD + reconcile/upsert
  discovery.py           # source adapters -> list[Record]; discover_all()
  namer.py               # assign names, collision handling, policy + serve generation
  health.py              # HealthChecker: HTTP GET each health_url -> up/down
  conformance.py         # coverage diff discovered vs stored
  api.py                 # FastAPI app (routes)
  index.py               # render_index(records) -> HTML string
  main.py                # uvicorn entrypoint
  tests/
    test_models.py
    test_store.py
    test_discovery.py
    test_namer.py
    test_health.py
    test_conformance.py
    test_index.py
    test_api.py
  deploy/
    kai-directory.service
    install.sh
```

Each file has one responsibility. `discovery.py` holds all source adapters but each is a pure function taking raw text/JSON (so it is unit-testable without the network).

---

### Task 1: Package skeleton + models

**Files:**
- Create: `services/kai_directory/__init__.py`
- Create: `services/kai_directory/models.py`
- Test: `services/kai_directory/tests/test_models.py`
- Create: `services/kai_directory/tests/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_models.py
from services.kai_directory.models import slugify, Record


def test_slugify_basic():
    assert slugify("Money Center") == "money-center"
    assert slugify("kai_betting") == "kai-betting"
    assert slugify("Directives!!") == "directives"


def test_slugify_collapses_and_trims():
    assert slugify("  a  --  b  ") == "a-b"
    assert slugify("") == ""


def test_record_defaults_and_url():
    r = Record(id="money", name="money", display_name="Money Center",
               category="finances", host="ct108", ip="192.168.1.118", port=8095)
    assert r.target_url == "http://192.168.1.118:8095"
    assert r.tailnet_name == ""
    assert r.health_status == "unknown"


def test_record_to_dict_roundtrip_fields():
    r = Record(id="bet", name="bet", display_name="KAI Bet", category="betting",
               host="ct111", ip="192.168.1.111", port=8000, bind="0.0.0.0")
    d = r.to_dict()
    assert d["id"] == "bet" and d["target_url"] == "http://192.168.1.111:8000"
    assert set(d) >= {"id", "name", "tailnet_name", "health_status", "source"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_models.py -v"'`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.kai_directory'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/__init__.py
"""Kai service directory: name + catalog every app/module."""
```

```python
# services/kai_directory/tests/__init__.py
```

```python
# services/kai_directory/models.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")


@dataclass
class Record:
    id: str
    name: str
    display_name: str = ""
    category: str = "general"
    host: str = ""
    container: str = ""
    ip: str = ""
    port: int = 0
    bind: str = ""
    tailnet_name: str = ""
    internal_url: str = ""
    proxy_node: str = ""
    health_url: str = ""
    health_status: str = "unknown"
    owner: str = ""
    tags: str = ""
    source: str = "manual"
    target_url: str = ""
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def __post_init__(self):
        self.port = int(self.port or 0)
        if not self.target_url and self.ip and self.port:
            self.target_url = f"http://{self.ip}:{self.port}"
        if not self.health_url and self.target_url:
            self.health_url = self.target_url

    def to_dict(self) -> dict:
        return asdict(self)
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): package skeleton + models\""'
```

---

### Task 2: RecordStore (SQLite CRUD + reconcile)

**Files:**
- Create: `services/kai_directory/store.py`
- Test: `services/kai_directory/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_store.py
import os
import tempfile

from services.kai_directory.models import Record
from services.kai_directory.store import RecordStore


def make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return RecordStore(path), path


def test_upsert_and_get():
    store, _ = make_store()
    store.upsert(Record(id="money", name="money", display_name="Money",
                        category="finances", host="ct108", ip="192.168.1.118", port=8095))
    got = store.get("money")
    assert got is not None and got.display_name == "Money"


def test_reconcile_marks_stale_but_keeps_records():
    store, _ = make_store()
    store.upsert(Record(id="money", name="money", ip="192.168.1.118", port=8095, source="docker"))
    store.upsert(Record(id="bet", name="bet", ip="192.168.1.111", port=8000, source="docker"))
    # next sweep only sees 'money'
    store.reconcile([Record(id="money", name="money", ip="192.168.1.118", port=8095, source="docker")])
    assert store.get("money") is not None
    assert store.get("bet") is not None          # kept, not wiped
    assert store.get("bet").source == "docker:stale"


def test_manual_records_survive_reconcile():
    store, _ = make_store()
    store.upsert(Record(id="manual-1", name="manual-1", source="manual"))
    store.reconcile([])
    assert store.get("manual-1") is not None


def test_list_and_delete():
    store, _ = make_store()
    store.upsert(Record(id="a", name="a", category="infra"))
    store.upsert(Record(id="b", name="b", category="infra"))
    assert len(store.list(category="infra")) == 2
    store.delete("a")
    assert store.get("a") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_store.py -v"'`
Expected: FAIL (`No module named ...store`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/store.py
from __future__ import annotations

import dataclasses
import sqlite3
from contextlib import contextmanager
from typing import Optional

from .models import Record

_COLS = [f.name for f in dataclasses.fields(Record)]
_AUTO_SOURCES = ("registry", "docker", "ss", "cloudflared", "panel")


class RecordStore:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.execute(f"CREATE TABLE IF NOT EXISTS services ({', '.join(col + ' TEXT' for col in _COLS)}, PRIMARY KEY (id))")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def upsert(self, r: Record) -> None:
        with self._conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO services ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' for _ in _COLS)})",
                [getattr(r, col) for col in _COLS],
            )

    def get(self, id_: str) -> Optional[Record]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM services WHERE id=?", (id_,)).fetchone()
        return Record(**{k: row[k] for k in _COLS}) if row else None

    def list(self, category: str = "", q: str = "") -> list[Record]:
        sql, args = "SELECT * FROM services", []
        where = []
        if category:
            where.append("category=?"); args.append(category)
        if q:
            where.append("(name LIKE ? OR display_name LIKE ? OR tags LIKE ?)")
            args += [f"%{q}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY category, name"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [Record(**{k: r[k] for k in _COLS}) for r in rows]

    def delete(self, id_: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM services WHERE id=?", (id_,))

    def reconcile(self, discovered: list[Record]) -> dict:
        seen = {r.id for r in discovered}
        for r in discovered:
            self.upsert(r)
        stale = 0
        for existing in self.list():
            if existing.id in seen:
                continue
            if existing.source == "manual":
                continue
            if any(existing.source.startswith(s) for s in _AUTO_SOURCES) and not existing.source.endswith(":stale"):
                existing.source = existing.source + ":stale"
                self.upsert(existing)
                stale += 1
        return {"seen": len(seen), "stale": stale}
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): SQLite record store with reconcile\""'
```

---

### Task 3: Discovery — registry + listeners (ss)

**Files:**
- Create: `services/kai_directory/discovery.py`
- Test: `services/kai_directory/tests/test_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_discovery.py
from services.kai_directory.discovery import (
    parse_registry, parse_ss, LOOPBACK_BIND,
)


def test_parse_registry_json():
    doc = {"services": [
        {"name": "kai-legal-brain", "host": "192.168.1.100", "port": 8100, "category": "legal"},
        {"name": "susu", "host": "192.168.1.111", "port": 8050, "category": "finances"},
    ]}
    recs = parse_registry(doc, host_label="ct")
    assert {r.name for r in recs} == {"kai-legal-brain", "susu"}
    assert recs[0].source == "registry"
    assert recs[0].port == 8100


def test_parse_ss_filters_loopback_and_keeps_wildcard():
    ss_output = (
        "LISTEN 0 2048 0.0.0.0:8099 0.0.0.0:* users:((\"uvicorn\",pid=1,fd=6))\n"
        "LISTEN 0 128 127.0.0.1:5432 0.0.0.0:* users:((\"postgres\",pid=2,fd=5))\n"
        "LISTEN 0 128 [::]:4000 [::]:*  users:((\"python3\",pid=3,fd=4))\n"
    )
    recs = parse_ss(ss_output, ip="192.168.1.111", host="ct111")
    ports = sorted(r.port for r in recs)
    assert ports == [4000, 8099]          # 5432 loopback dropped
    assert all(r.source == "ss" for r in recs)
    assert recs[0].bind in ("0.0.0.0", "::")


def test_loopback_bind_constant():
    assert "127.0.0.1" in LOOPBACK_BIND
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_discovery.py -v"'`
Expected: FAIL (`No module named ...discovery`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/discovery.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): registry + ss listeners discovery\""'
```

---

### Task 4: Discovery — docker + cloudflared + discover_all runner

**Files:**
- Modify: `services/kai_directory/discovery.py`
- Test: `services/kai_directory/tests/test_discovery.py`

- [ ] **Step 1: Write the failing test (append to test_discovery.py)**

```python
from services.kai_directory.discovery import (
    parse_docker, parse_cloudflared, parse_panels, discover_all,
)


def test_parse_docker_ps():
    out = (
        "kai-money-money-center-1\t0.0.0.0:8095->8095/tcp\tkai-money-money-center\n"
        "kai-money-db-1\t5432/tcp\tpostgres:16-alpine\n"
    )
    recs = parse_docker(out, ip="192.168.1.118", host="ct108")
    assert len(recs) == 1                      # db has no published port
    assert recs[0].port == 8095
    assert recs[0].source == "docker"
    assert "money-center" in recs[0].name


def test_parse_cloudflared_ingress():
    yml = """
ingress:
  - hostname: command.deerude.com
    service: http://localhost:80
  - hostname: careers.deerude.com
    service: http://192.168.1.115:4000
  - service: http_status:404
"""
    recs = parse_cloudflared(yml)
    assert {r.display_name for r in recs} == {"command.deerude.com", "careers.deerude.com"}
    assert all(r.source == "cloudflared" for r in recs)


def test_parse_panels():
    html = '<a data-hash="#money">Money</a><a data-hash="#legal">Legal</a><a href="x">no</a>'
    recs = parse_panels(html)
    assert {r.name for r in recs} == {"money", "legal"}
    assert all(r.source == "panel" for r in recs)


def test_discover_all_never_raises(monkeypatch):
    monkeypatch.setattr("services.kai_directory.discovery._run", lambda *a, **k: "")
    monkeypatch.setattr("services.kai_directory.discovery._read_json", lambda *a, **k: None)
    monkeypatch.setattr("services.kai_directory.discovery._read_text", lambda *a, **k: "")
    assert discover_all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: same pytest command
Expected: FAIL (`cannot import name 'parse_docker'`)

- [ ] **Step 3: Write minimal implementation (append to discovery.py)**

```python
import os


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
        short = cname.replace("kai-money-", "").replace("-1", "")
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
        out.append(Record(id=f"panel-{name}", name=slugify(f"panel-{name}"),
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
    """Collect from all sources. Remote host listeners come from the
    orchestrator's service_registry export (KAI_SERVICES_JSON); this host's
    listeners and docker come from local commands; cloudflared + CC panels
    come from their files. Any source failing yields no records (never raises).
    """
    recs: list[Record] = []
    for p in REGISTRY_PATHS:
        recs += parse_registry(_read_json(p))
    recs += parse_ss(_run(["ss", "-ltn"]), ip=SELF_IP, host="ct114")
    docker_out = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}\t{{.Image}}"])
    if docker_out:
        recs += parse_docker(docker_out, ip=SELF_IP, host="ct114")
    for d in CLOUDFLARED_DIRS:
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith((".yml", ".yaml")):
                    recs += parse_cloudflared(_read_text(os.path.join(d, fn)))
    recs += parse_panels(_read_text(PANEL_PATH))
    # dedupe by id (first source wins)
    seen, dedup = set(), []
    for r in recs:
        if r.id not in seen:
            seen.add(r.id); dedup.append(r)
    return dedup
```

> **Why remote listeners come from the registry JSON, not `ss` over SSH:** the
> directory LXC does not have SSH access to every container. The orchestrator's
> existing `service_registry` already performs per-host listener discovery; its
> export is synced to `/var/lib/kai-directory/kai_services.json` by a cron/sync
> step in Task 11 Step 5. This keeps one discovery authority for remote hosts
> and the directory as the single catalog.

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): docker + cloudflared discovery and runner\""'
```

---

### Task 5: Namer (name assignment + policy + serve generation)

**Files:**
- Create: `services/kai_directory/namer.py`
- Test: `services/kai_directory/tests/test_namer.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_namer.py
from services.kai_directory.models import Record
from services.kai_directory.namer import (
    assign_names, policy_block, serve_commands, SUFFIX,
)


def test_assign_names_basic():
    recs = [Record(id="money", name="money"), Record(id="bet", name="bet")]
    out = assign_names(recs, proxy_for={"money": "proxmox-b", "bet": "proxmox-b"})
    assert out[0].tailnet_name == f"money.{SUFFIX}"
    assert out[0].internal_url == f"https://money.{SUFFIX}/"
    assert out[0].proxy_node == "proxmox-b"


def test_assign_names_collision_suffix():
    recs = [Record(id="money", name="money"), Record(id="money-2", name="money")]
    out = assign_names(recs, proxy_for={})
    names = [r.name for r in out]
    assert names[0] == "money"
    assert names[1] == "money-2"


def test_policy_block_shape():
    recs = [Record(id="money", name="money", tailnet_name=f"money.{SUFFIX}")]
    blk = policy_block(recs)
    assert "svc:money" in blk
    assert '"tcp:443"' in blk


def test_serve_commands_shape():
    recs = [Record(id="money", name="money", target_url="http://192.168.1.118:8095",
                   proxy_node="proxmox-b")]
    cmds = serve_commands(recs, node="proxmox-b")
    assert cmds and cmds[0] == "tailscale serve --bg --service=svc:money http://192.168.1.118:8095"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_namer.py -v"'`
Expected: FAIL (`No module named ...namer`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/namer.py
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
        if r.proxy_node == node and r.target_url and r.tailnet_name
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): name assignment + policy/serve generation\""'
```

---

### Task 6: Health checker

**Files:**
- Create: `services/kai_directory/health.py`
- Test: `services/kai_directory/tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_health.py
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from services.kai_directory.models import Record
from services.kai_directory.health import classify, check_all


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a):
        pass


def test_classify():
    assert classify(200) == "up"
    assert classify(401) == "up"        # reachable, auth-gated
    assert classify(500) == "down"
    assert classify(None) == "down"


def test_check_all_sets_status():
    srv = HTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    recs = [Record(id="a", name="a", health_url=f"http://127.0.0.1:{port}/"),
            Record(id="b", name="b", health_url="http://127.0.0.1:1/")]
    out = check_all(recs, timeout=2)
    assert out[0].health_status == "up"
    assert out[1].health_status == "down"
    srv.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_health.py -v"'`
Expected: FAIL (`No module named ...health`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/health.py
from __future__ import annotations

import urllib.request

from .models import Record


def classify(code) -> str:
    if code is None:
        return "down"
    return "up" if code < 500 else "down"


def check_one(rec: Record, timeout: float = 5.0) -> str:
    url = rec.health_url or rec.target_url
    if not url:
        return "unknown"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return classify(r.status)
    except urllib.error.HTTPError as e:
        return classify(e.code)
    except Exception:
        return "down"


def check_all(records: list[Record], timeout: float = 5.0) -> list[Record]:
    for r in records:
        r.health_status = check_one(r, timeout)
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): health checker\""'
```

---

### Task 7: Conformance check

**Files:**
- Create: `services/kai_directory/conformance.py`
- Test: `services/kai_directory/tests/test_conformance.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_conformance.py
from services.kai_directory.models import Record
from services.kai_directory.conformance import check


def test_check_reports_missing():
    discovered = [Record(id="a", name="a"), Record(id="b", name="b")]
    stored = [Record(id="a", name="a")]
    report = check(discovered, stored)
    assert report["ok"] is False
    assert report["missing"] == ["b"]


def test_check_passes_when_covered():
    discovered = [Record(id="a", name="a")]
    stored = [Record(id="a", name="a")]
    assert check(discovered, stored)["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_conformance.py -v"'`
Expected: FAIL (`No module named ...conformance`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/conformance.py
from __future__ import annotations

from .models import Record


def check(discovered: list[Record], stored: list[Record]) -> dict:
    stored_ids = {r.id for r in stored}
    missing = sorted({r.id for r in discovered if r.id not in stored_ids})
    return {
        "ok": not missing,
        "discovered": len({r.id for r in discovered}),
        "stored": len(stored_ids),
        "missing": missing,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): conformance coverage check\""'
```

---

### Task 8: Index renderer

**Files:**
- Create: `services/kai_directory/index.py`
- Test: `services/kai_directory/tests/test_index.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_index.py
from services.kai_directory.models import Record
from services.kai_directory.index import render_index


def test_render_lists_services_and_links():
    recs = [Record(id="money", name="money", display_name="Money Center",
                   category="finances", internal_url="https://money.tail82a9ca.ts.net/",
                   health_status="up")]
    html = render_index(recs)
    assert "Money Center" in html
    assert "https://money.tail82a9ca.ts.net/" in html
    assert "up" in html.lower()


def test_render_escapes_html():
    recs = [Record(id="x", name="x", display_name="<script>bad</script>")]
    html = render_index(recs)
    assert "<script>bad</script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_index.py -v"'`
Expected: FAIL (`No module named ...index`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/index.py
from __future__ import annotations

import html

from .models import Record

_CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#0d1117;color:#e6edf3}
header{padding:16px 24px;background:#161b22;border-bottom:1px solid #30363d}
h1{margin:0;font-size:18px}
main{max-width:1000px;margin:0 auto;padding:16px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:8px;border-bottom:1px solid #21262d;text-align:left}
.up{color:#3fb950}.down{color:#f85149}.unknown{color:#8b949e}
a{color:#58a6ff;text-decoration:none}
"""


def render_index(records: list[Record]) -> str:
    rows = []
    for r in records:
        url = r.internal_url or r.target_url
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.display_name or r.name)}</td>"
            f"<td>{html.escape(r.category)}</td>"
            f"<td class=\"{html.escape(r.health_status)}\">{html.escape(r.health_status)}</td>"
            f"<td>{html.escape(r.host or '')}:{r.port}</td>"
            f"<td><a href=\"{html.escape(url)}\">{html.escape(url)}</a></td>"
            "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Kai Service Directory</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<header><h1>Kai Service Directory</h1></header><main>"
        "<table><thead><tr><th>Service</th><th>Category</th><th>Status</th>"
        "<th>Host</th><th>URL</th></tr></thead><tbody>"
        + "".join(rows) +
        "</tbody></table></main></body></html>"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): HTML index renderer\""'
```

---

### Task 9: API + entrypoint

**Files:**
- Create: `services/kai_directory/api.py`
- Create: `services/kai_directory/main.py`
- Test: `services/kai_directory/tests/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_api.py
import os
import tempfile

from fastapi.testclient import TestClient

from services.kai_directory.api import create_app
from services.kai_directory.store import RecordStore


def client_with_store():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    return TestClient(create_app(RecordStore(path)))


def test_health_and_services_list():
    c = client_with_store()
    assert c.get("/health").json()["ok"] is True
    assert c.get("/services").json() == []


def test_create_get_delete():
    c = client_with_store()
    body = {"id": "money", "name": "money", "display_name": "Money",
            "category": "finances", "ip": "192.168.1.118", "port": 8095}
    assert c.post("/services", json=body).status_code == 201
    assert c.get("/services/money").json()["display_name"] == "Money"
    assert c.delete("/services/money").status_code == 200
    assert c.get("/services/money").status_code == 404


def test_policy_endpoint_returns_json():
    c = client_with_store()
    body = {"id": "money", "name": "money", "ip": "192.168.1.118", "port": 8095}
    c.post("/services", json=body)
    assert "svc:money" in c.get("/policy").text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_api.py -v"'`
Expected: FAIL (`No module named ...api`)

- [ ] **Step 3: Write minimal implementation**

```python
# services/kai_directory/api.py
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse

from .conformance import check
from .discovery import discover_all
from .health import check_all
from .index import render_index
from .models import Record
from .namer import assign_names, policy_block, serve_commands, SUFFIX
from .store import RecordStore


def create_app(store: RecordStore) -> FastAPI:
    app = FastAPI(title="Kai Service Directory")

    @app.get("/health")
    def health():
        return {"ok": True, "services": len(store.list())}

    @app.get("/services")
    def list_services(category: str = "", q: str = ""):
        return [r.to_dict() for r in store.list(category=category, q=q)]

    @app.get("/services/{id_}")
    def get_service(id_: str):
        r = store.get(id_)
        if not r:
            raise HTTPException(404, "not found")
        return r.to_dict()

    @app.post("/services", status_code=201)
    def create_service(body: dict):
        body["source"] = body.get("source", "manual")
        store.upsert(Record(**{k: v for k, v in body.items()
                               if k in Record.__dataclass_fields__}))
        return store.get(body["id"]).to_dict()

    @app.put("/services/{id_}")
    def update_service(id_: str, body: dict):
        body["id"] = id_
        store.upsert(Record(**{k: v for k, v in body.items() if k in Record.__dataclass_fields__}))
        return store.get(id_).to_dict()

    @app.delete("/services/{id_}")
    def delete_service(id_: str):
        if not store.get(id_):
            raise HTTPException(404, "not found")
        store.delete(id_)
        return {"deleted": id_}

    @app.post("/discover")
    def discover():
        recs = discover_all()
        result = store.reconcile(recs)
        return {"discovered": len(recs), **result}

    @app.post("/health/refresh")
    def refresh():
        recs = check_all(store.list())
        for r in recs:
            store.upsert(r)
        return {r.id: r.health_status for r in recs}

    @app.get("/conformance")
    def conformance():
        return check(discover_all(), store.list())

    @app.get("/policy")
    def policy():
        return Response(policy_block(store.list()), media_type="application/json")

    @app.get("/serve-commands")
    def serve_commands_ep(node: str):
        return serve_commands(store.list(), node)

    @app.get("/export")
    def export(format: str = "json"):
        rows = [r.to_dict() for r in store.list()]
        if format == "yaml":
            lines = []
            for r in rows:
                lines.append(f"- id: {r['id']}")
                for k, v in r.items():
                    if k != "id":
                        lines.append(f"  {k}: {v}")
            return Response("\n".join(lines) + "\n", media_type="text/yaml")
        return rows

    @app.get("/", response_class=HTMLResponse)
    def index():
        return render_index(store.list())

    return app
```

```python
# services/kai_directory/main.py
from __future__ import annotations

import os

import uvicorn

from .api import create_app
from .store import RecordStore

DB = os.environ.get("KAI_DIRECTORY_DB", "/var/lib/kai-directory/services.db")


def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    app = create_app(RecordStore(DB))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("KAI_DIRECTORY_PORT", "8097")))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_api.py -v"'`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(kai-directory): FastAPI app + entrypoint\""'
```

---

### Task 10: Deploy unit + install script, run full test suite

**Files:**
- Create: `services/kai_directory/deploy/kai-directory.service`
- Create: `services/kai_directory/deploy/install.sh`
- Test: full suite

- [ ] **Step 1: Write the systemd unit**

```ini
# services/kai_directory/deploy/kai-directory.service
[Unit]
Description=Kai Service Directory
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/kai-directory
Environment=KAI_DIRECTORY_DB=/var/lib/kai-directory/services.db
Environment=KAI_DIRECTORY_PORT=8097
Environment=PYTHONPATH=/opt/kai-directory
ExecStart=/opt/kai-directory/.venv/bin/python -m kai_directory.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write the install script**

```sh
#!/bin/sh
# services/kai_directory/deploy/install.sh
set -e
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip
mkdir -p /opt/kai-directory /var/lib/kai-directory
cp -r services/kai_directory /opt/kai-directory/kai_directory
python3 -m venv /opt/kai-directory/.venv
/opt/kai-directory/.venv/bin/pip install -q fastapi uvicorn
cp services/kai_directory/deploy/kai-directory.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kai-directory.service
sleep 2
systemctl is-active kai-directory.service
curl -s -m 5 http://127.0.0.1:8097/health; echo
```

- [ ] **Step 3: Run the full suite**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests -q"'`
Expected: PASS (25 tests)

- [ ] **Step 4: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"chore(kai-directory): systemd unit + install script\""'
```

---

### Task 11: Create the LXC and deploy

**Files:** none (infrastructure)

- [ ] **Step 1: Create LXC 114 on PVE-B**

```bash
$PVEB 'pct create 114 local:vztmpl/debian-12-standard_*.tar.zst \
  --hostname kai-directory --cores 2 --memory 1024 --swap 512 \
  --rootfs local-lvm:8 --net0 name=eth0,bridge=vmbr0,ip=192.168.1.114/24,gw=192.168.1.1 \
  --unprivileged 1 --features nesting=1 --onboot 1 --start 1'
```
Expected: `pct status 114` → `status: running`

> If no `local:vztmpl/debian-12-*` template exists, download it first:
> `$PVEB 'pveam update && pveam download local debian-12-standard_12.7-1_amd64.tar.zst'`

- [ ] **Step 2: Deploy code + start service**

```bash
$PVEB 'pct push 111 /opt/ai-orchestrator/services /tmp/svc.tar'
# simpler: copy the package tree and run install.sh inside 114
$PVEB 'tar -C /opt/ai-orchestrator -cf /tmp/kai_directory.tar services/kai_directory'
$PVEB 'pct push 111 /tmp/kai_directory.tar /tmp/kai_directory.tar'
$PVEB 'pct exec 114 -- mkdir -p /opt/kai-directory'
$PVEB 'pct push 114 /tmp/kai_directory.tar /tmp/kai_directory.tar'
$PVEB 'pct exec 114 -- sh -c "tar -C /tmp -xf /tmp/kai_directory.tar && cd services/kai_directory && sh deploy/install.sh"'
```
Expected: `active` and `{"ok":true,...}`

- [ ] **Step 3: Verify from CT111**

```bash
$PVEB 'pct exec 111 -- curl -s -m 5 http://192.168.1.114:8097/health'
```
Expected: `{"ok":true,"services":0}`

- [ ] **Step 4: Sync discovery inputs to the directory LXC**

```bash
# service_registry export (remote-host listeners) + the CC HTML (panel list)
$PVEB 'pct exec 111 -- cp /opt/ai-orchestrator/memory/kai_services.json /tmp/kai_services.json'
$PVEB 'pct exec 111 -- cp /opt/ai-orchestrator/core/kai/command_center.html /tmp/cc.html'
$PVEB 'pct push 111 /tmp/kai_services.json /tmp/kai_services.json'
$PVEB 'pct push 111 /tmp/cc.html /tmp/cc.html'
$PVEB 'pct push 114 /tmp/kai_services.json /var/lib/kai-directory/kai_services.json'
$PVEB 'pct push 114 /tmp/cc.html /var/lib/kai-directory/command_center.html'
$PVEB 'pct exec 114 -- sh -c "wc -c /var/lib/kai-directory/kai_services.json /var/lib/kai-directory/command_center.html"'
```
Expected: both files present and non-zero.

- [ ] **Step 5: Run discovery + populate**

```bash
$PVEB 'pct exec 114 -- curl -s -X POST http://127.0.0.1:8097/discover'
$PVEB 'pct exec 114 -- curl -s http://127.0.0.1:8097/health'
```
Expected: `services` > 20

- [ ] **Step 6: Add a daily sync timer (keep inputs fresh)**

```bash
$PVEB 'pct exec 114 -- sh -c "cat >/etc/systemd/system/kai-directory-sync.service <<EOF
[Unit]
Description=Refresh kai-directory discovery inputs
[Service]
Type=oneshot
ExecStart=/bin/sh -c \"curl -s -X POST http://127.0.0.1:8097/discover >/dev/null\"
EOF
cat >/etc/systemd/system/kai-directory-sync.timer <<EOF
[Unit]
Description=Daily kai-directory sync
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload && systemctl enable --now kai-directory-sync.timer && systemctl list-timers kai-directory-sync.timer --no-pager | tail -2"'
```
Expected: timer listed as active.

---

### Task 12: Apply naming (policy + serve) — operator-assisted

**Files:** none (infrastructure)

- [ ] **Step 1: Generate the policy block**

```bash
$PVEB 'pct exec 114 -- curl -s http://127.0.0.1:8097/policy' > /tmp/kai_services_policy.json
cat /tmp/kai_services_policy.json
```

- [ ] **Step 2: Operator pastes the `services:` block into the tailnet policy**

Add under the existing policy (admin console → Access controls):
```json
"services": {
  "svc:money": { "endpoints": ["tcp:443"] },
  "svc:bet": { "endpoints": ["tcp:443"] }
}
```
Expected: admin console saves without error.

- [ ] **Step 3: Run serve commands on the proxy node**

```bash
$PVEB 'pct exec 114 -- curl -s "http://127.0.0.1:8097/serve-commands?node=proxmox-b"'
# then execute each returned command on PVE-B:
$PVEB 'tailscale serve --bg --service=svc:money http://192.168.1.118:8095'
$PVEB 'tailscale serve status'
```

- [ ] **Step 4: Verify a name resolves from a second tailnet node**

```bash
$PVEB 'ssh -o BatchMode=yes root@100.83.4.27 "curl -s -o /dev/null -w \"%{http_code}\n\" https://money.tail82a9ca.ts.net/"'
```
Expected: `200` (or `401` if the service gates auth — both are success)

- [ ] **Step 5: Front PVE-A services on the PVE-A proxy node**

```bash
# names whose proxy_node is proxmox-a (PVE-A-hosted apps, e.g. command, talent)
$PVEB 'pct exec 114 -- curl -s "http://127.0.0.1:8097/serve-commands?node=proxmox-a"'
# execute each on PVE-A:
$PVEB 'ssh -o BatchMode=yes root@100.83.4.27 "tailscale serve --bg --service=svc:command http://192.168.1.99.11:80 && tailscale serve status"'
```
Expected: `serve status` lists the PVE-A names; a second tailnet node returns 200/401.

---

### Task 13: Command Center panel

**Files:**
- Modify: `core/cc_extra_routes.py` (add `/api/directory/*` proxy)
- Modify: `core/kai/command_center.html` (nav + panel + dispatcher)
- Test: `services/kai_directory/tests/test_cc_proxy.py`

- [ ] **Step 1: Write the failing test**

```python
# services/kai_directory/tests/test_cc_proxy.py
import core.cc_extra_routes as cc


def test_directory_proxy_target_constant():
    assert cc.DIRECTORY_BASE == "http://192.168.1.114:8097"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests/test_cc_proxy.py -v"'`
Expected: FAIL (`AttributeError: module ... has no attribute 'DIRECTORY_BASE'`)

- [ ] **Step 3: Add the proxy constant + routes to cc_extra_routes.py**

Add near the other module proxies:
```python
DIRECTORY_BASE = os.environ.get("KAI_DIRECTORY_BASE", "http://192.168.1.114:8097")
```
and register:
```python
@app.get("/api/directory/services")
async def directory_services(category: str = "", q: str = ""):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{DIRECTORY_BASE}/services", params={"category": category, "q": q})
    return JSONResponse(r.json())

@app.get("/api/directory/conformance")
async def directory_conformance():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{DIRECTORY_BASE}/conformance")
    return JSONResponse(r.json())
```
(use the same `httpx`/`JSONResponse` imports already present in `cc_extra_routes.py`)

- [ ] **Step 4: Run test to verify it passes**

Run: same pytest command → Expected: PASS

- [ ] **Step 5: Add the CC panel**

In `command_center.html`: add nav item `#directory`, `<div id="panel-directory">`, a `PANEL_TITLES` entry `directory: "Service Directory"`, and a `loadDirectory()` that fetches `/api/directory/services` and renders the same table as the index (search input + category filter). Wire it into the existing dispatcher alongside the other panels.

- [ ] **Step 6: Verify panel renders**

Run: open Command Center → `directory`; confirm rows load and the conformance badge is green.
Expected: all services listed, no console errors.

- [ ] **Step 7: Commit**

```bash
$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && git add core/cc_extra_routes.py core/kai/command_center.html services/kai_directory && git -c user.name=\"Kai Agent\" -c user.email=\"kai-agent@local\" commit -m \"feat(cc): service directory panel + proxy\""'
```

---

### Task 14: Final verification

**Files:** none

- [ ] **Step 1: Full test suite green**

Run: `$PVEB 'pct exec 111 -- sh -c "cd /opt/ai-orchestrator && .venv/bin/python -m pytest services/kai_directory/tests -q"'`
Expected: PASS, 0 failures.

- [ ] **Step 2: Conformance green (nothing missing)**

Run: `$PVEB 'pct exec 114 -- curl -s http://127.0.0.1:8097/conformance'`
Expected: `{"ok": true, "missing": []}`. If not, register the missing services (POST /services) or add them to the deny list, then re-run.

- [ ] **Step 3: Every tailnet name resolves**

```bash
$PVEB 'pct exec 114 -- curl -s "http://127.0.0.1:8097/serve-commands?node=proxmox-b" | head'
$PVEB 'tailscale serve status'
```
Expected: every advertised name listed in `serve status`.

- [ ] **Step 4: Directory index reachable**

```bash
$PVEB 'ssh -o BatchMode=yes root@100.83.4.27 "curl -s -o /dev/null -w \"%{http_code}\n\" https://directory.tail82a9ca.ts.net/"'
```
Expected: `200`

---

## Notes for the executor

- Run every command from LXC113 unless stated. `$PVEB` is the SSH prefix defined in the header.
- TDD order is mandatory: failing test → run → implement → pass → commit.
- The directory service has **no external LLM** dependency (consistent with Kai policy).
- Do not store secrets in the directory store.
- If Tailscale Services cannot be enabled, use the Serve-path fallback: set `internal_url` to `https://proxmox-b.tail82a9ca.ts.net/<name>/` in `namer.py` and serve with `tailscale serve --bg --set-path=/<name> http://<target>`.
