"""Kai Free Coding Model Manager.

Autonomous discovery, validation, benchmarking, selection, rotation, and failover
for genuinely free OpenRouter models suitable for coding.

Architecture:
- discovery.py   — OpenRouter API model discovery + pricing verification
- validator.py  — Real inference testing + multi-stage coding benchmarks
- scorer.py     — Scoring engine (coding capability + overall ranking)
- router.py     — Active model pool + automatic failover + circuit breaker
- notifier.py   — Telegram notifications via kai-notify hub
- api.py        — REST endpoints + Telegram command interface
- scheduler.py  — Scheduled discovery cycles + health checks
- models.py     — Persistent model database (SQLite)
"""

import os
from pathlib import Path

# Directly read .env file to get the OpenRouter key
# (dotenv.load_dotenv may not override existing process env vars)
def _read_env_key(key: str, default: str = "") -> str:
    # __file__ = /project/ai-orchestrator/core/free_model_manager/__init__.py
    # .parent.parent.parent = /project/ai-orchestrator
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                value = line.split("=", 1)[1].strip()
                # Remove surrounding quotes
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                return value
    return default

# Read OpenRouter key directly from .env file (bypasses process env)
OPENROUTER_API_KEY = _read_env_key("OPENROUTER_API_KEY")

# Module root
MODULE_DIR = Path(__file__).parent.resolve()
DATA_DIR = MODULE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Database path
DB_PATH = DATA_DIR / "models.db"

# Configuration
OMNIROUTE_BASE_URL = _read_env_key("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
FREE_CODING_PORT = int(_read_env_key("FREE_CODING_PORT", "8096"))

# Scoring weights (configurable)
SCORING_WEIGHTS = {
    "coding_capability": 0.25,
    "agentic_coding": 0.20,
    "reasoning": 0.15,
    "tool_calling": 0.10,
    "reliability": 0.15,
    "latency": 0.05,
    "context": 0.05,
    "output_quality": 0.05,
}

# Thresholds
MIN_CODING_SCORE = 5.0  # Models must score > 5.0/10 to enter pool
MIN_IMPROVEMENT_THRESHOLD = 0.05  # 5% improvement required for promotion

# Circuit breaker
CIRCUIT_BREAKER_FAILURES = 3
CIRCUIT_BREAKER_WINDOW_MS = 5 * 60 * 1000  # 5 minutes
CIRCUIT_BREAKER_COOLDOWN_MS = 15 * 60 * 1000  # 15 minutes

# Schedules
DISCOVERY_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours
HEALTH_CHECK_INTERVAL_SECONDS = 15 * 60  # 15 minutes

# Kai Notify integration
NOTIFY_URL = os.getenv("KAI_NOTIFY_URL", "http://localhost:8094")
NOTIFY_TOKEN = os.getenv("KAI_NOTIFY_TOKEN", "")

# Backup configuration
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
MAX_BACKUPS = 10

# Logging
LOG_PATH = DATA_DIR / "free_model_manager.log"
