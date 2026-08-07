#!/usr/bin/env python3
"""Kai System Cleanup — periodic waste removal and state trimming.

Audit-documented bloat sources (2026-08-07), each with a safe upper bound.
Run daily via cron; idempotent and safe to run more often.

Freed 10.5GB on first run. Ongoing waste is ~100-500MB/week from:
  - pip/npm cache accumulation
  - browser automation cache (puppeteer/playwright)
  - unbounded growth of memory JSON files

All file sizes in bytes unless labelled otherwise.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — thresholds and limits
# ---------------------------------------------------------------------------

# Directories safe to purge entirely (rebuilt on demand)
PURGE_DIRS = [
    "/root/.cache/pip",
    "/root/.cache/puppeteer",
    "/root/.cache/ms-playwright",
    "/root/.cache/node-gyp",
    "/root/.npm/_npx",
]

# Directories to cap at a max size (delete oldest first)
CAP_DIRS = {
    "/root/.cache/huggingface": 50 * 1024**2,   # 50MB max
}

# Memory JSONs to trim — keep the last N records
JSON_TRIM = {
    "memory/builds_archive.json": 100,      # keep 100 most recent builds
    "memory/incidents.json": 500,           # keep 500 most recent incidents
    "memory/ai_usage_history.json": 2000,   # keep 2000 most recent calls
    "memory/remediation_history.json": 300,
    "memory/approval_queue.json": 200,
}

# Files exceeding these sizes get trimmed regardless
JSON_MAX_SIZE = 50 * 1024**2  # 50MB — any JSON over this gets trimmed

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_PATH = Path(__file__).parent.parent / "logs" / "kai_cleanup.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cleanup] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cleanup")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def dir_size(path: str) -> int:
    """Return total size of directory in bytes, 0 if missing."""
    if not os.path.isdir(path):
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def human(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024**2:
        return f"{n/1024:.0f}KB"
    if n < 1024**3:
        return f"{n/1024**2:.0f}MB"
    return f"{n/1024**3:.1f}GB"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def purge_dirs():
    """Remove directories that are safe to recreate on demand."""
    for path in PURGE_DIRS:
        if not os.path.exists(path):
            continue
        before = dir_size(path)
        try:
            shutil.rmtree(path)
            log.info(f"purged {path} ({human(before)})")
        except OSError as e:
            log.warning(f"failed to purge {path}: {e}")


def trim_pip_cache():
    """Run pip cache purge — removes unused wheels/tarballs."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "cache", "purge"],
            capture_output=True, text=True, timeout=30,
        )
        removed = 0
        for line in result.stdout.splitlines() + result.stderr.splitlines():
            if "removed" in line.lower():
                try:
                    removed = int(line.split("removed")[-1].strip().split()[0])
                except ValueError:
                    pass
        if removed > 0:
            log.info(f"pip cache purge: {removed} files removed")
    except Exception as e:
        log.warning(f"pip cache purge failed: {e}")


def trim_npm_cache():
    """Run npm cache clean."""
    npm = shutil.which("npm")
    if not npm:
        return
    try:
        subprocess.run([npm, "cache", "clean", "--force"], capture_output=True, timeout=30)
        log.info("npm cache cleaned")
    except Exception as e:
        log.warning(f"npm cache clean failed: {e}")


def trim_memory_jsons(base_dir: str):
    """Keep only the last N records in each tracked memory JSON file."""
    for rel_path, keep in JSON_TRIM.items():
        path = os.path.join(base_dir, rel_path)
        if not os.path.isfile(path):
            continue
        before = os.path.getsize(path)
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"skipping {rel_path}: {e}")
            continue

        records = data.get("records", [])
        if not isinstance(records, list) or len(records) <= keep:
            continue

        data["records"] = records[-keep:]
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        after = os.path.getsize(path)
        log.info(f"trimmed {rel_path}: {len(records)}→{keep} records ({human(before)}→{human(after)})")


def trim_oversized_jsons(base_dir: str):
    """Scan memory/ for any JSON exceeding JSON_MAX_SIZE and trim it."""
    mem_dir = os.path.join(base_dir, "memory")
    if not os.path.isdir(mem_dir):
        return
    for name in os.listdir(mem_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(mem_dir, name)
        size = os.path.getsize(path)
        if size < JSON_MAX_SIZE:
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"skipping oversized {name}: {e}")
            continue

        records = data.get("records", [])
        if not isinstance(records, list) or len(records) < 100:
            log.warning(f"oversized {name} ({human(size)}) has no 'records' list to trim")
            continue
        data["records"] = records[-100:]
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        after = os.path.getsize(path)
        log.info(f"oversized trim {name}: {human(size)}→{human(after)}")


def remove_old_backups(base_dir: str, keep_days: int = 7):
    """Remove backup tarballs older than keep_days."""
    paths = [
        os.path.join(base_dir, f) for f in os.listdir(base_dir)
        if f.endswith(".tar.gz") and "backup" in f.lower()
    ]
    for path in paths:
        try:
            age = datetime.now().timestamp() - os.path.getmtime(path)
            if age > keep_days * 86400:
                size = os.path.getsize(path)
                os.remove(path)
                log.info(f"removed old backup {os.path.basename(path)} ({human(size)}, {age/86400:.0f}d old)")
        except OSError as e:
            log.warning(f"failed to remove backup {path}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_cleanup():
    started = datetime.now(timezone.utc)
    log.info("=== cleanup cycle starting ===")

    base_dir = Path(__file__).parent.parent

    purge_dirs()
    trim_pip_cache()
    trim_npm_cache()
    trim_memory_jsons(str(base_dir))
    trim_oversized_jsons(str(base_dir))
    remove_old_backups(str(base_dir))

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(f"=== cleanup cycle finished ({elapsed:.1f}s) ===")


if __name__ == "__main__":
    run_cleanup()
