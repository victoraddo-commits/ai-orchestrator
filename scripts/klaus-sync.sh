#!/bin/bash
# Auto-sync KLAUS PostgreSQL → Legal Brain SQLite WORM store.
# Runs idempotently — only inserts new documents, skips existing by content hash.
# Called by cron every 2 hours.

set -euo pipefail

PROJECT=/project/ai-orchestrator
LOG_DIR=/var/log/ai-orchestrator
mkdir -p "$LOG_DIR"

exec "$PROJECT/.venv/bin/python" -m core.klaus.migrate_pg_to_sqlite \
    >> "$LOG_DIR/klaus-sync.log" 2>&1
