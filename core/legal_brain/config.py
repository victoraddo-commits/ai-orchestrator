"""Legal Brain configuration — dedicated storage paths and DB settings.

Permanent and Temporary stores use SEPARATE SQLite databases
and SEPARATE filesystem paths. No shared state.
"""

import os
from pathlib import Path

# Base directory for all Legal Brain data
LEGAL_BRAIN_ROOT = Path(
    os.environ.get("LEGAL_BRAIN_ROOT", "/var/lib/ai-orchestrator/legal_brain")
)

# Permanent store — immutable, WORM, hash-chained
PERMANENT_DB_PATH = LEGAL_BRAIN_ROOT / "permanent" / "legal_brain.db"
PERMANENT_STORAGE_ROOT = LEGAL_BRAIN_ROOT / "permanent" / "documents"

# Temporary workspace — isolated, ephemeral
WORKSPACE_ROOT = LEGAL_BRAIN_ROOT / "workspace"
WORKSPACE_TTL_SECONDS = int(os.environ.get("LEGAL_BRAIN_WORKSPACE_TTL", str(7 * 86400)))  # 7 days

# Knowledge graph
KNOWLEDGE_DB_PATH = LEGAL_BRAIN_ROOT / "knowledge" / "knowledge_graph.db"

# Backups
BACKUP_ROOT = LEGAL_BRAIN_ROOT / "backups"

# Integrity
INTEGRITY_CHECK_INTERVAL = int(os.environ.get("LEGAL_BRAIN_INTEGRITY_INTERVAL", "86400"))  # daily

# Sandbox
SANDBOX_TIMEOUT_SECONDS = int(os.environ.get("LEGAL_BRAIN_SANDBOX_TIMEOUT", "30"))
SANDBOX_MAX_MEMORY_MB = int(os.environ.get("LEGAL_BRAIN_SANDBOX_MAX_MEMORY_MB", "512"))
SANDBOX_MAX_FILE_SIZE_MB = int(os.environ.get("LEGAL_BRAIN_MAX_UPLOAD_SIZE_MB", "50"))

# ClamAV
CLAMAV_ENABLED = os.environ.get("LEGAL_BRAIN_CLAMAV_ENABLED", "0") == "1"
CLAMAV_SOCKET = os.environ.get("LEGAL_BRAIN_CLAMAV_SOCKET", "/var/run/clamav/clamd.ctl")

# Ghana-only jurisdiction (Phase 1)
DEFAULT_JURISDICTION = "Ghana"
ALLOWED_JURISDICTIONS = frozenset({"Ghana"})


def ensure_directories():
    """Create all required Legal Brain directories."""
    for d in [
        PERMANENT_DB_PATH.parent,
        PERMANENT_STORAGE_ROOT,
        WORKSPACE_ROOT,
        KNOWLEDGE_DB_PATH.parent,
        BACKUP_ROOT,
    ]:
        d.mkdir(parents=True, exist_ok=True)
