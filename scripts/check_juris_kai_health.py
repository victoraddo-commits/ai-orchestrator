#!/usr/bin/env python3
"""Check Juris Kai bot health via the /tmp/juris-kai-health touch file.

If the file is missing or older than MAX_AGE_SECONDS, send a Telegram alert
via the existing bridge.  Runs from cron; silent on success.

Intended cron: */5 * * * * root /project/ai-orchestrator/.venv/bin/python /project/ai-orchestrator/scripts/check_juris_kai_health.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/project/ai-orchestrator")

HEALTH_FILE = Path("/tmp/juris-kai-health")
MAX_AGE_SECONDS = 600  # 10 minutes — poll cycle is ~1s, so 10min means bot is stuck

# --- Don't alert if the bot isn't supposed to be running ---
# If the systemd service is stopped intentionally, the health file
# won't be touched.  Check if the service is active before alerting.
SERVICE_ACTIVE = False
try:
    import subprocess
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", "ai-orchestrator-juris-kai"],
        capture_output=True,
    )
    SERVICE_ACTIVE = result.returncode == 0
except Exception:
    pass  # If we can't check, assume it should be running

if not SERVICE_ACTIVE:
    # Service intentionally stopped — clean up stale health file, don't alert
    if HEALTH_FILE.exists():
        HEALTH_FILE.unlink(missing_ok=True)
    sys.exit(0)

# --- Check health file ---
if not HEALTH_FILE.exists():
    # Bot never touched the health file at all
    from core.telegram_bridge import send_message
    send_message("⚠️ Juris Kai bot — health file MISSING. Bot may have failed to start.")
    sys.exit(1)

age = time.time() - HEALTH_FILE.stat().st_mtime
if age > MAX_AGE_SECONDS:
    from core.telegram_bridge import send_message
    send_message(f"⚠️ Juris Kai bot appears DOWN — health file is {age/60:.0f} min stale (threshold: {MAX_AGE_SECONDS/60:.0f} min)")
    sys.exit(1)

# Healthy — silent exit
sys.exit(0)
