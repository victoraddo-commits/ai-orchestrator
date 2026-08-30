"""Persistent network topology graph — atomic read/write via Kai's memory layer."""

import os, shutil
from pathlib import Path
from datetime import datetime, timezone

# Resolve memory/ relative to this file (core/) unless overridden by env var
def _get_memory_dir() -> Path:
    env = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    if env:
        return Path(env)
    return Path(__file__).parent.parent / "memory"

def _get_graph_file() -> Path:
    return _get_memory_dir() / "network_topology.json"

def _get_bak_file() -> Path:
    return _get_memory_dir() / "network_topology.json.bak"

_SCHEMA_VERSION = 1


def load_graph() -> dict:
    """Load the current topology graph. Returns empty structure if none exists."""
    graph_file = _get_graph_file()
    if not graph_file.exists():
        return _empty_graph()
    try:
        import json
        with open(graph_file) as f:
            return json.load(f)
    except Exception:
        return _empty_graph()


def load_prior() -> dict | None:
    """Load the prior snapshot (.bak). Returns None if no backup exists."""
    bak_file = _get_bak_file()
    if not bak_file.exists():
        return None
    try:
        import json
        with open(bak_file) as f:
            return json.load(f)
    except Exception:
        return None


def save_graph(graph: dict) -> None:
    """Atomically save graph: copy prior to .bak → write temp → os.replace."""
    memory_dir = _get_memory_dir()
    graph_file = _get_graph_file()
    bak_file = _get_bak_file()
    graph["schema_version"] = _SCHEMA_VERSION
    graph["generated_at"] = datetime.now(timezone.utc).isoformat()
    memory_dir.mkdir(parents=True, exist_ok=True)
    # Backup prior (if exists) before overwriting
    if graph_file.exists():
        shutil.copy2(graph_file, bak_file)
    # Atomic write via temp + replace
    tmp = graph_file.with_suffix(".tmp")
    import json
    tmp.write_text(json.dumps(graph, indent=2))
    os.replace(tmp, graph_file)
    # Ensure .bak exists after every save (copy current if no prior existed)
    if not bak_file.exists():
        shutil.copy2(graph_file, bak_file)


def _empty_graph() -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sites": {},
        "tailscale": {"peers": {}, "subnet_routes": {}},
        "tunnel": {"status": "UNKNOWN", "a_to_b_latency_ms": None,
                   "b_to_a_latency_ms": None, "packet_loss_pct": None,
                   "last_test": None},
        "connectivity": {},
        "last_discovery": None,
        "last_change": None,
    }


def get_schema_version() -> int:
    return _SCHEMA_VERSION
