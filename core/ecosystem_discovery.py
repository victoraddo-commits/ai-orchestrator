"""Ecosystem discovery scanner — walks /project/src and builds the initial ecosystem graph.

Covers:
- Module discovery (every top-level src/ subdirectory)
- Secret/key storage detection
- Telegram bot detection
- Notification system detection
- Docker container mapping
- Import/call graph
"""

import os
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SRC_ROOT = Path("/project/src")
ORCHESTRATOR_ROOT = Path("/project/ai-orchestrator")

# ── Module discovery ──────────────────────────────────────────────────────────

def scan_src_directory(root: Path = SRC_ROOT) -> list[dict]:
    """Scan src/ subdirectories and classify each as an entity."""
    entities = []
    if not root.exists():
        return entities
    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir.name.startswith(".") or subdir.name in ("node_modules", "dist", "__pycache__", ".pytest_cache"):
            continue
        entity = _classify_module(subdir)
        if entity:
            entities.append(entity)
    return entities

def _classify_module(path: Path) -> dict | None:
    """Classify a module directory into an entity."""
    name = path.name
    readme = path / "README.md"
    desc = ""
    if readme.exists():
        first_lines = readme.read_text().split("\n")[:3]
        desc = " ".join(l.lstrip("# ").strip() for l in first_lines[:2]).strip()

    if name.startswith("kai-"):
        if name == "kai-vault":
            etype = "capability_owner"
            canonical = True
        elif name in ("kai-notify", "kai-audit", "kai-agent"):
            etype = "service"
            canonical = False
        else:
            etype = "application"
            canonical = False
    elif name in ("ai-orchestrator", "ai-orchestrator-plugin"):
        etype = "agent"
        canonical = False
    elif name in ("it-manager", "talent", "proxdash", "susu", "deerude-theme", "claudecodeui"):
        etype = "application"
        canonical = False
    elif name == "telegra-approval-responder":
        etype = "service"
        canonical = False
    else:
        etype = "unknown"
        canonical = False

    return {
        "id": name,
        "type": etype,
        "name": name.replace("-", " ").replace("_", " ").title(),
        "description": desc,
        "canonical_owner": canonical,
        "status": "active",
        "path": str(path),
    }

# ── Secret store detection ────────────────────────────────────────────────────

SECRET_PATTERNS = [
    re.compile(r"api_key|apiKey|apikey", re.I),
    re.compile(r"secret|SECRET", re.I),
    re.compile(r"password|PASSWORD|passwd", re.I),
    re.compile(r"token|TOKEN", re.I),
    re.compile(r"vault|VAULT", re.I),
]

JSON_SECRET_FILES = [
    "provider_secrets.json",
    "secrets.json",
    "secrets.yml",
    "secrets.py",
    ".env",
    "credentials.json",
]

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB — skip files larger than this

def find_secret_stores(path: Path) -> list[dict]:
    """Detect secret/key storage in a file or directory."""
    stores = []
    if path.is_file():
        return _detect_secret_store_in_file(path)
    elif path.is_dir():
        for f in path.rglob("*"):
            if not (f.is_file() and _should_scan_file(f)):
                continue
            stores.extend(_detect_secret_store_in_file(f))
    return stores

def _should_scan_file(f: Path) -> bool:
    """Return True if the file should be scanned for secrets."""
    skip_dirs = {"node_modules", "dist", "__pycache__", ".pytest_cache", ".git", "target", ".venv", ".eggs"}
    skip_suffixes = {".pyc", ".pyo", ".so", ".rlib", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".bin", ".ttf", ".otf", ".woff", ".woff2", ".cur"}
    if any(part in skip_dirs for part in f.parts):
        return False
    if f.suffix.lower() in skip_suffixes:
        return False
    return True

def _entity_from_path(f: Path) -> str:
    """Extract entity name from a file path relative to /project."""
    rel = f.relative_to(Path("/project"))
    parts = rel.parts
    # /project/ai-orchestrator/core/ai/secrets.py → "ai-orchestrator"
    # /project/src/kai-vault/... → "kai-vault"
    if len(parts) < 2:
        return "unknown"
    if parts[0] in ("ai-orchestrator", "src"):
        return parts[1]
    return parts[0]

def _detect_secret_store_in_file(f: Path) -> list[dict]:
    stores = []
    try:
        if f.stat().st_size > MAX_FILE_SIZE:
            return stores
        content = f.read_text(errors="ignore")
    except Exception:
        return stores

    if f.name in JSON_SECRET_FILES or f.name.startswith(".env"):
        stores.append({
            "id": f"file:{f.relative_to(Path('/project'))}",
            "type": "secret_store",
            "format": f.suffix.lstrip("."),
            "entity": _entity_from_path(f),
            "path": str(f),
        })
        return stores

    hits = sum(1 for p in SECRET_PATTERNS if p.search(content))
    if hits >= 3:
        stores.append({
            "id": f"inline:{f.relative_to(Path('/project'))}",
            "type": "secret_store",
            "format": "inline",
            "entity": _entity_from_path(f),
            "path": str(f),
            "pattern_hits": hits,
        })
    return stores

# ── Telegram bot detection ────────────────────────────────────────────────────

TELEGRAM_PATTERNS = [
    re.compile(r"telegram|Telegram|TG_|sendMessage|send_message|botToken|bot_token", re.I),
    re.compile(r"TelegraClient|Telegra|ApprovalResponder", re.I),
]

def find_telegram_bots(path: Path) -> list[dict]:
    """Detect Telegram bot implementations."""
    bots = []
    if path.is_file():
        return _detect_telegram_in_file(path)
    elif path.is_dir():
        for f in path.rglob("*"):
            if f.is_file() and _should_scan_file(f):
                bots.extend(_detect_telegram_in_file(f))
    return bots

def _detect_telegram_in_file(f: Path) -> list[dict]:
    bots = []
    try:
        if f.stat().st_size > MAX_FILE_SIZE:
            return bots
        content = f.read_text(errors="ignore")
    except Exception:
        return bots

    hits = sum(1 for p in TELEGRAM_PATTERNS if p.search(content))
    if hits >= 2:
        bots.append({
            "id": f"bot:{f.relative_to(Path('/project'))}",
            "platform": "telegram",
            "entity": _entity_from_path(f),
            "path": str(f),
            "pattern_hits": hits,
        })
    return bots

# ── Notification system detection ─────────────────────────────────────────────

NOTIFY_PATTERNS = [
    re.compile(r"notify|Notify|notification|Notification", re.I),
    re.compile(r"hub|aggregator|Aggregator", re.I),
]

def find_notification_systems(root: Path = SRC_ROOT) -> list[dict]:
    """Find systems that aggregate or send notifications."""
    systems = []
    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(".") or subdir.name in ("node_modules", "dist", "__pycache__"):
            continue
        try:
            files = list(subdir.rglob("*"))
        except Exception:
            continue
        notify_files = [f for f in files if f.is_file() and not any(p in str(f) for p in ("node_modules", "dist", "__pycache__", ".git"))]
        for f in notify_files:
            try:
                content = f.read_text(errors="ignore")
            except Exception:
                continue
            if NOTIFY_PATTERNS[0].search(content) or NOTIFY_PATTERNS[1].search(content):
                systems.append({
                    "id": subdir.name,
                    "type": "notification_system",
                    "path": str(subdir),
                })
                break
    return systems

# ── Docker service mapping ────────────────────────────────────────────────────

def find_docker_services() -> list[dict]:
    """Query docker ps to find running containers and map them to entities."""
    import subprocess
    services = []
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            name = parts[0]
            status = parts[1] if len(parts) > 1 else "unknown"
            entity_id = _docker_name_to_entity(name)
            services.append({
                "docker_name": name,
                "entity_id": entity_id,
                "status": status,
                "running": "Up" in status,
            })
    except Exception:
        pass
    return services

def _docker_name_to_entity(name: str) -> str:
    """Map a docker container name to an entity ID."""
    name = name.lower()
    if "vault" in name:
        return "kai-vault"
    if "notify" in name:
        return "kai-notify"
    if "audit" in name:
        return "kai-audit"
    if "money" in name or "franklin" in name or "freqtrade" in name:
        return "kai-money"
    if "bet" in name:
        return "kai-betting"
    if "talent" in name:
        return "talent"
    if "it-manager" in name or "itmanager" in name:
        return "it-manager"
    if "proxdash" in name:
        return "proxdash"
    if "telegra" in name or "approval" in name:
        return "telegra-approval-responder"
    return name

# ── Build initial graph ──────────────────────────────────────────────────────

def build_initial_graph() -> dict:
    """Run all discovery scans and build the initial ecosystem graph."""
    now = datetime.now(timezone.utc).isoformat()
    entities = {}
    capabilities = {}
    relationships = []

    for entity in scan_src_directory():
        entities[entity["id"]] = entity

    orch = {
        "id": "ai-orchestrator",
        "type": "agent",
        "name": "AI Orchestrator",
        "description": "Autonomous infra ops + app builder platform",
        "canonical_owner": False,
        "status": "active",
        "path": "/project/ai-orchestrator",
    }
    entities["ai-orchestrator"] = orch

    secret_entities = {}
    for store in find_secret_stores(SRC_ROOT):
        entity_id = store.get("entity", "unknown")
        if entity_id not in secret_entities:
            secret_entities[entity_id] = {
                "id": f"{entity_id}-secrets",
                "type": "capability_owner",
                "name": f"{entity_id.title()} Secrets",
                "canonical_owner": False,
                "deprecated_by": "kai-vault" if entity_id != "kai-vault" else None,
                "status": "deprecated" if entity_id != "kai-vault" else "locked",
                "description": f"Inline or file-based secret storage in {entity_id}",
            }
    for ent in secret_entities.values():
        entities[ent["id"]] = ent

    for bot in find_telegram_bots(SRC_ROOT):
        rel = {
            "from": bot["entity"],
            "to": "kai-vault",
            "type": "auth_with",
            "description": f"Telegram bot in {bot['entity']}",
        }
        relationships.append(rel)

    for sys in find_notification_systems():
        if sys["id"] not in entities:
            entities[sys["id"]] = {
                "id": sys["id"],
                "type": "service",
                "name": sys["id"].replace("-", " ").title(),
                "canonical_owner": False,
                "status": "active",
            }
        if sys["id"] != "kai-vault":
            relationships.append({
                "from": sys["id"],
                "to": "notification",
                "type": "notifies",
                "description": "Sends notifications",
            })

    for svc in find_docker_services():
        if svc["entity_id"] in entities:
            entities[svc["entity_id"]]["docker_name"] = svc["docker_name"]
            entities[svc["entity_id"]]["docker_status"] = svc["status"]
            if svc["running"]:
                entities[svc["entity_id"]]["status"] = "active"
            else:
                entities[svc["entity_id"]]["status"] = "stopped"

    CAPABILITIES = [
        {"id": "secret-management", "name": "Secret Management", "canonical_owner": "kai-vault", "deprecated_owners": ["orchestrator-secrets"], "status": "migrating"},
        {"id": "notification", "name": "Notification / Alerting", "canonical_owner": "kai-notify", "deprecated_owners": [], "status": "active"},
        {"id": "telegram-messaging", "name": "Telegram Messaging", "canonical_owner": "kai-notify", "deprecated_owners": ["telegra-approval-responder"], "status": "active"},
        {"id": "observability-audit", "name": "Observability / Audit", "canonical_owner": "kai-audit", "deprecated_owners": [], "status": "active"},
        {"id": "ai-routing", "name": "AI Model Routing", "canonical_owner": "ai-orchestrator", "deprecated_owners": [], "status": "active"},
    ]
    for cap in CAPABILITIES:
        capabilities[cap["id"]] = cap

    KNOWN_RELS = [
        {"from": "ai-orchestrator", "to": "orchestrator-secrets", "type": "reads_from", "description": "AI router reads provider keys from JSON store"},
        {"from": "kai-money", "to": "kai-vault", "type": "reads_from", "description": "Money center reads keys via vault reveal API"},
        {"from": "kai-notify", "to": "kai-vault", "type": "auth_with", "description": "Notify hub reads tokens from vault"},
        {"from": "telegra-approval-responder", "to": "ai-orchestrator", "type": "reads_from", "description": "Reads approval queue from orchestrator memory"},
    ]
    for rel in KNOWN_RELS:
        if rel["from"] in entities and rel["to"] in entities:
            relationships.append(rel)

    return {
        "schema_version": 1,
        "entities": entities,
        "capabilities": capabilities,
        "relationships": relationships,
        "last_updated": now,
    }
