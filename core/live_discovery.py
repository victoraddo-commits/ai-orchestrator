"""Live-estate capability discovery (KAI 2.0 upgrade).

Unlike ecosystem_discovery (master-repo layout), this scans the RUNNING
estate: deployed core modules and active services. Machine-readable output
per the capability-inventory schema.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_CORE_ROOT = Path("/opt/ai-orchestrator/core")
_NOISE = ("bak", "backup", "pycache", ".pyc")


def _modules() -> list[dict]:
    out = []
    entries = sorted(_CORE_ROOT.glob("*.py")) + sorted(
        d for d in _CORE_ROOT.iterdir()
        if d.is_dir() and (d / "__init__.py").exists())
    for p in entries:
        n = p.name
        if any(s in n.lower() for s in _NOISE):
            continue
        out.append({
            "capability_id": f"core:{p.stem}",
            "name": p.stem,
            "module": "core",
            "location": str(p),
            "status": "discovered",
            "purpose": _purpose(p),
            "last_verified": None,
        })
    return out


def _services() -> list[dict]:
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--state=running",
             "--no-legend", "--no-pager"],
            capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        unit = line.split()[0] if line.split() else ""
        if not unit or ".service" not in unit:
            continue
        name = unit.replace(".service", "")
        out.append({
            "capability_id": f"service:{name}",
            "name": name,
            "module": "services",
            "location": f"systemd:{unit}",
            "status": "running",
            "purpose": "",
            "last_verified": None,
        })
    return out


def scan_live_estate(core_root: str | None = None) -> list[dict]:
    """Return capability records for deployed modules + running services."""
    caps = _modules()
    caps.extend(_services())
    return caps



def _purpose(p) -> str:
    """First docstring line (<=120 chars) or '' when absent/unparsable."""
    try:
        if p.is_dir():
            p = p / "__init__.py"
        text = p.read_text(encoding="utf-8", errors="ignore")[:4000]
        idx = text.find('"""')
        if idx == -1:
            return ""
        nxt = text.find('"""', idx + 3)
        if nxt == -1:
            return ""
        first = text[idx + 3:nxt].strip().splitlines()[0].strip()
        return first[:120]
    except Exception:
        return ""
