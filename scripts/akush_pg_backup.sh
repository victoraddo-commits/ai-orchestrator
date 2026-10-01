#!/bin/bash
# akush_pg_backup.sh — Phase 8 §58 encrypted app-level backup of kai_money.
#
# pg_dump kai_money -> gzip -> openssl enc aes-256-cbc (PBKDF2) using the key
# from kai-vault secrets/money/backup_key (fetched at runtime, never printed,
# never written to disk). Output lives alongside the kai-backup archives in
# /opt/ai-orchestrator/backups/akush-pg/. Retention: keep 7 most recent.
# Integrity: the new archive is decrypt-tested to /dev/null + gzip -t at
# creation; every retained archive is re-verified on each run.
#
# Restore drill (§60 mandate): restore into throwaway DB
# kai_money_restore_test, sanity SELECTs (table count matches source),
# then DROP. See akush-pg-restore-test.sh.
set -u
umask 077

BACKUP_DIR="${AKUSH_PG_BACKUP_DIR:-/opt/ai-orchestrator/backups/akush-pg}"
RETENTION="${AKUSH_PG_BACKUP_RETENTION:-7}"
LOG() { printf '{"t":"%s","level":"%s","msg":"%s"%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3"; }

mkdir -p "$BACKUP_DIR"

# --- key from vault (never printed, never written to disk) -------------------
KEY="$(cd /opt/ai-orchestrator && /opt/ai-orchestrator/.venv/bin/python - <<'PYEOF'
import os
from core.ai import kai_vault_client as v
tok = v.load_token()
print(v.fetch_secret("secrets/money/backup_key", token=tok) or "")
PYEOF
)"
if [ -z "$KEY" ]; then
  LOG error "backup key unavailable from vault — aborting" ''
  exit 1
fi
export AKUSH_BK="$KEY"
ENC_ARGS=(-aes-256-cbc -pbkdf2 -iter 600000)

# --- dump + gzip + encrypt ---------------------------------------------------
TS="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/akush-kai_money-${TS}.sql.gz.enc"
if su - postgres -c 'pg_dump kai_money' \
   | gzip -1 \
   | openssl enc "${ENC_ARGS[@]}" -salt -pass env:AKUSH_BK -out "$TARGET.part" 2>/dev/null; then
  mv "$TARGET.part" "$TARGET"
else
  rm -f "$TARGET.part"
  LOG error "dump/encrypt failed" ''
  exit 1
fi
SIZE="$(stat -c %s "$TARGET")"

# --- dump-time reference counts (§60: the restore drill must compare the
# restored snapshot against the counts captured AT DUMP TIME, not against the
# live DB, which keeps moving and would false-fail the drill) ---------------
SRC_TABLES="$(su - postgres -c "psql -At -d kai_money -c \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'\"")"
SRC_SMS="$(su - postgres -c "psql -At -d kai_money -c 'SELECT COUNT(*) FROM sms_messages'")"
printf '{"archive":"akush-kai_money-%s","src_tables":%s,"src_sms":%s}\n' \
  "$TS" "${SRC_TABLES:-null}" "${SRC_SMS:-null}" \
  > "$BACKUP_DIR/akush-kai_money-${TS}.meta.json"

# --- integrity: decrypt-test to /dev/null + gzip -t ---------------------------
if ! openssl enc -d "${ENC_ARGS[@]}" -pass env:AKUSH_BK -in "$TARGET" 2>/dev/null | gzip -t; then
  LOG error "integrity check failed for created archive" ''
  exit 1
fi

# --- retention: keep newest N -------------------------------------------------
cd "$BACKUP_DIR" || exit 1
ls -1t akush-kai_money-*.sql.gz.enc 2>/dev/null | tail -n +$((RETENTION + 1)) | while read -r old; do
  base="${old%.sql.gz.enc}"
  rm -f -- "$old" "$base.meta.json"
done

# --- re-verify every retained archive ----------------------------------------
FAILS=0
for f in akush-kai_money-*.sql.gz.enc; do
  [ -e "$f" ] || continue
  openssl enc -d "${ENC_ARGS[@]}" -pass env:AKUSH_BK -in "$f" 2>/dev/null | gzip -t 2>/dev/null \
    || { FAILS=$((FAILS + 1)); LOG warn "archive corrupt: $f" ''; }
done

echo "akush-kai_money-${TS}" > "$BACKUP_DIR/LATEST"
LOG info "backup ok" ",\"file\":\"akush-kai_money-${TS}.sql.gz.enc\",\"size_bytes\":${SIZE},\"retention_failures\":${FAILS}"
