#!/usr/bin/env python3
"""One-shot cleanup for the 2026-09-20 Kai alert flood.

Root cause: core/approval_watchdog._send_with_incident_fallback() embedded the
whole reminder body -- including a per-cycle duration like "failed and
unaddressed for 986h" -- in the incident ISSUE text. create_incident() dedupes
on the exact issue string, so every cycle minted a new incident: 1,025 open
telegram incidents at ~21/hour, forever.

This script (run once, after the code fix is live):
  1. Backs up memory/incidents.json (and the memory_manager .bak is kept too).
  2. Marks the legacy reminder-failure incidents resolved with a note.
  3. Runs bounded retention (archive resolved incidents older than N days and
     collapse duplicate open incidents).

Idempotent: re-running on an already-cleaned file changes nothing.
"""

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.incident_manager import (  # noqa: E402
    load_incidents,
    prune_incidents,
    resolve_stale_reminder_incidents,
)


RESOLUTION_NOTE = (
    "Auto-resolved 2026-09-20: alert-flood root cause fixed (variable duration "
    "was embedded in the deduped issue text; now a stable issue + recurrence "
    "cooldown). No operator action required."
)

RETENTION_DAYS = 7


def _counts(incidents):
    resolved = sum(1 for i in incidents if i.get("status") in ("resolved", "closed"))
    return {
        "total": len(incidents),
        "open": len(incidents) - resolved,
        "resolved": resolved,
    }


def main():
    incidents_path = ROOT / "memory" / "incidents.json"

    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"incidents.json.pre-flood-cleanup-{stamp}.bak"

    if incidents_path.exists():
        shutil.copyfile(incidents_path, backup_path)

    before = _counts(load_incidents())

    resolved = resolve_stale_reminder_incidents(RESOLUTION_NOTE)
    retention = prune_incidents(resolved_older_than_days=RETENTION_DAYS)

    after = _counts(load_incidents())

    print(f"backup:            {backup_path}")
    print(f"before:            {before}")
    print(f"resolved legacy:   {resolved}")
    print(f"retention:         {retention}")
    print(f"after:             {after}")


if __name__ == "__main__":
    main()
