"""Orphan retirement verdicts — classification only, never deletion.

For every orphan module reported by core.live_dupes.find_orphans(), scan the
entire /opt/ai-orchestrator tree (.py under core, plus /etc/ai-orchestrator.env
and *.json/*.yaml under /opt/ai-orchestrator) for dynamic references:

  - "import_module('core.X')" / importlib usage near the name
  - quoted 'X' or 'core.X' in config text

Verdict: KEEP when any evidence is found, PROPOSE_RETIRE when there is none.
A verdict of 'RETIRED' is never emitted here (classification only).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.live_dupes import find_orphans

ORCH_ROOT = Path("/opt/ai-orchestrator")
CORE_DIR = ORCH_ROOT / "core"
ENV_FILE = Path("/etc/ai-orchestrator.env")

_VERDICTS = {"KEEP", "PROPOSE_RETIRE"}


def _scan_files() -> list[Path]:
    files = [f for f in CORE_DIR.rglob("*.py")
             if "__pycache__" not in f.parts and not f.name.endswith("bak")]
    files.append(ENV_FILE)
    files.extend(ORCH_ROOT.rglob("*.json"))
    files.extend(ORCH_ROOT.rglob("*.yaml"))
    return [f for f in files if f.exists()]


def _text_for(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def find_evidence(module: str, scan: list[Path] | None = None) -> list[str]:
    """Return evidence lines referencing module dynamically anywhere."""
    if module.startswith("core:"):
        module = module.split(":", 1)[1]
    if scan is None:
        scan = _scan_files()
    esc = re.escape(module)
    pats = [
        re.compile(r"import_module\(\s*['\"](?:core\.)?" + esc + r"['\"]"),
        re.compile(r"importlib.{0,100}" + esc, re.DOTALL),
        re.compile(r"['\"](?:core\.)?" + esc + r"['\"]"),
    ]
    hint = re.compile(esc)
    out = []
    for f in scan:
        text = _text_for(f)
        if not text or not hint.search(text):
            continue
        rel = str(f)
        for i, line in enumerate(text.splitlines(), 1):
            for p in pats:
                if p.search(line):
                    out.append(f"{f.name}:{i}: {line.strip()[:160]}")
                    break
    return out


def verdict_for(module: str, scan: list[Path] | None = None) -> dict:
    ev = find_evidence(module, scan)
    verdict = "KEEP" if ev else "PROPOSE_RETIRE"
    return {
        "module": module,
        "verdict": verdict,
        "evidence": ev if ev else ["no dynamic reference found in core/*.py, env, json/yaml"],
    }


def build_verdicts() -> list[dict]:
    scan = _scan_files()
    return [verdict_for(m, scan) for m in find_orphans()]


def format_table(rows: list[dict]) -> str:
    w = max((len(r["module"]) for r in rows), default=6)
    lines = [f"{'module'.ljust(w)}  verdict          evidence"]
    lines.append("-" * (w + 60))
    for r in rows:
        ev = r["evidence"][0] if len(r["evidence"]) == 1 else \
            f"{len(r['evidence'])} refs, e.g. {r['evidence'][0]}"
        lines.append(f"{r['module'].ljust(w)}  {r['verdict'].ljust(16)} {ev}")
    return "\n".join(lines)


def dump_json(path: Path) -> None:
    json.dump(build_verdicts(), path.open("w"), indent=2)
