#!/bin/bash
# akush-pg-restore-test.sh — §60 restore drill for the encrypted kai_money
# backup. Decrypts the LATEST archive, restores into THROWAWAY DB
# kai_money_restore_test, runs sanity SELECTs (table count + sms_messages
# count must match the source DB), then drops the throwaway DB.
set -u
umask 077

BACKUP_DIR="${AKUSH_PG_BACKUP_DIR:-/opt/ai-orchestrator/backups/akush-pg}"
RESTORE_DB="kai_money_restore_test"
LOG() { printf '{"t":"%s","level":"%s","msg":"%s"%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3"; }

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

LATEST="$(cat "$BACKUP_DIR/LATEST" 2>/dev/null)"
ARCHIVE="$BACKUP_DIR/${LATEST}.sql.gz.enc"
if [ ! -e "$ARCHIVE" ]; then
  LOG error "no archive to restore-test" ''
  exit 1
fi

# source reference counts
SRC_TABLES="$(su - postgres -c "psql -At -d kai_money -c \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'\"")"
SRC_SMS="$(su - postgres -c "psql -At -d kai_money -c 'SELECT COUNT(*) FROM sms_messages'")"

# throwaway restore
su - postgres -c "psql -qc \"DROP DATABASE IF EXISTS $RESTORE_DB\" postgres >/dev/null" || true
su - postgres -c "psql -qc \"CREATE DATABASE $RESTORE_DB\" postgres >/dev/null" || {
  LOG error "could not create $RESTORE_DB" ''; exit 1; }

openssl enc -d "${ENC_ARGS[@]}" -pass env:AKUSH_BK -in "$ARCHIVE" 2>/dev/null \
  | gzip -dc \
  | su - postgres -c "psql -q -v ON_ERROR_STOP=1 -d $RESTORE_DB" >/dev/null 2>&1
RC=$?

if [ $RC -ne 0 ]; then
  su - postgres -c "psql -qc \"DROP DATABASE IF EXISTS $RESTORE_DB\" postgres >/dev/null"
  LOG error "restore failed rc=${RC}" ''
  exit 1
fi

DST_TABLES="$(su - postgres -c "psql -At -d $RESTORE_DB -c \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'\"")"
DST_SMS="$(su - postgres -c "psql -At -d $RESTORE_DB -c 'SELECT COUNT(*) FROM sms_messages'")"

su - postgres -c "psql -qc \"DROP DATABASE IF EXISTS $RESTORE_DB\" postgres >/dev/null"

if [ "$SRC_TABLES" = "$DST_TABLES" ] && [ "$SRC_SMS" = "$DST_SMS" ]; then
  LOG info "restore test PASS" ",\"src_tables\":${SRC_TABLES},\"dst_tables\":${DST_TABLES},\"src_sms\":${SRC_SMS},\"dst_sms\":${DST_SMS},\"archive\":\"${LATEST}\""
  exit 0
else
  LOG error "restore test MISMATCH" ",\"src_tables\":${SRC_TABLES},\"dst_tables\":${DST_TABLES},\"src_sms\":${SRC_SMS},\"dst_sms\":${DST_SMS},\"archive\":\"${LATEST}\""
  exit 1
fi
