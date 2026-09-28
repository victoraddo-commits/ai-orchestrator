#!/bin/bash
# Kai juris nightly eval — fast-tier golden run + regression alert.
#
# Fast tier: runner.py --phase legal --limit-per-cat 2 (103 cases, ~35 min).
# Runs the cache warmer first so the judge/research model is hot (keep_alive).
#
# Tally: PASS count is compared against the LAST stored tally in
# data/juris_eval_last.json ({date, total, pass}). Every real run stores its
# new tally. Alert rules (raw pass counts, only when totals are comparable):
#   pass drops >= 5  -> Telegram "JURIS EVAL REGRESSION: X/total (was Y)"
#   pass improves    -> quiet Telegram "JURIS EVAL improved: X/total"
#   stable           -> log only
# If the case total changes materially (>10) the new tally re-seats the
# baseline without an alert (different suite size is not a regression).
#
# Dry-run (no model traffic, last.json never written):
#   JURIS_EVAL_DRYRUN=1|stable  replay stored tally through the alert logic
#   JURIS_EVAL_DRYRUN=regress   replay with pass = last - 6 (fires the alert)
#   JURIS_EVAL_DRYRUN=improve   replay with pass = last + 1 (quiet message)
# Telegram token/chat are read from /etc/ai-orchestrator.env INSIDE this
# container by the python sender; no secrets are printed or exported.

set -u

APP="/opt/ai-orchestrator"
LOGDIR="$APP/logs"
DATADIR="$APP/data"
LAST="$DATADIR/juris_eval_last.json"
RESULTS="/tmp/juris_cert_results.json"
ENVFILE="/etc/ai-orchestrator.env"
DRYRUN="${JURIS_EVAL_DRYRUN:-0}"
TODAY="$(date +%F)"
LOG="$LOGDIR/juris_eval_${TODAY}.log"
mkdir -p "$LOGDIR" "$DATADIR"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [juris-eval] $*" | tee -a "$LOG"
}

# Telegram sender: reads KAI_TELEGRAM_BOT_TOKEN / KAI_TELEGRAM_CHAT_ID from
# $ENVFILE inside CT111. Prints SENT / ERROR lines to stdout (no secrets).
tg_send() {
  python3 -c '
import sys, time, urllib.parse, urllib.request

ENVFILE = "/etc/ai-orchestrator.env"

def pick(name):
    for line in open(ENVFILE, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith(name + "="):
            value = line.split("=", 1)[1].strip().strip("\"").strip("\x27")
            if value:
                return value
    return None

token = pick("KAI_TELEGRAM_BOT_TOKEN")
chat = pick("KAI_TELEGRAM_CHAT_ID")
if not token or not chat:
    print("ERROR: missing token/chat in", ENVFILE)
    sys.exit(1)

payload = urllib.parse.urlencode({
    "chat_id": chat,
    "text": sys.argv[1],
}).encode("utf-8")

url = "https://api.telegram.org/bot%s/sendMessage" % token
for attempt in range(2):
    try:
        with urllib.request.urlopen(url, data=payload, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if "\"ok\":true" in body:
            print("SENT")
            sys.exit(0)
        sys.exit(1)
    except Exception as exc:
        if attempt == 1:
            print("ERROR:", exc)
            sys.exit(1)
        time.sleep(2)
' "$1"
}

# Read the stored tally: prints "PASS TOTAL DATE" (0 0 "" when absent).
read_tally() {
  python3 -c '
import json
try:
    with open("/opt/ai-orchestrator/data/juris_eval_last.json") as f:
        d = json.load(f)
    print(int(d.get("pass", 0)), int(d.get("total", 0)), str(d.get("date", "")))
except Exception:
    print("0 0 ")
'
}

# Write the tally: args = date total pass.
write_tally() {
  python3 -c '
import json, sys
json.dump({"date": sys.argv[1], "total": int(sys.argv[2]), "pass": int(sys.argv[3])},
          open(sys.argv[4], "w"))
' "$1" "$2" "$3" "$LAST"
}

# Extract the tally the runner just produced: "PASS TOTAL". Prefers the
# summary JSON the runner rewrites every run; falls back to log line counts.
extract_tally() {
  local t
  t=$(python3 -c '
import json
try:
    with open("/tmp/juris_cert_results.json") as f:
        d = json.load(f)
    e = [x for x in d if x.get("phase") == "legal"][-1]
    print(int(e["passed"]), int(e["total"]))
except Exception:
    print("", "")
')
  NEWPASS="$(echo "$t" | awk '{print $1}')"
  NEWTOTAL="$(echo "$t" | awk '{print $2}')"
  if [ -z "${NEWPASS:-}" ]; then
    NEWPASS="$(grep -c '^\[PASS\]' "$LOG" || true)"
    NEWTOTAL=$(( $(grep -c '^\[PASS\]' "$LOG" || true) + $(grep -c '^\[FAIL\]' "$LOG" || true) ))
  fi
}

# Alert logic: needs LASTPASS LASTTOTAL NEWPASS NEWTOTAL STORE (1=persist).
eval_alert_logic() {
  if [ "$NEWTOTAL" -le 0 ]; then
    log "no usable tally produced (dryrun=$DRYRUN) — nothing stored"
    return 1
  fi
  if [ "$LASTTOTAL" -gt 0 ]; then
    SPAN=$(( NEWTOTAL > LASTTOTAL ? NEWTOTAL - LASTTOTAL : LASTTOTAL - NEWTOTAL ))
  else
    SPAN=999
  fi
  if [ "$LASTPASS" -le 0 ] || [ "$SPAN" -gt 10 ]; then
    if [ "$LASTPASS" -le 0 ]; then
      log "no prior tally — storing baseline ${NEWPASS}/${NEWTOTAL}"
    else
      log "tally total changed (was ${LASTPASS}/${LASTTOTAL}, now ${NEWPASS}/${NEWTOTAL}) — re-seating baseline, no alert"
    fi
    if [ "$STORE" = 1 ]; then write_tally "$TODAY" "$NEWTOTAL" "$NEWPASS"; fi
    return 0
  fi
  if [ $(( LASTPASS - NEWPASS )) -ge 5 ]; then
    MSG="JURIS EVAL REGRESSION: ${NEWPASS}/${NEWTOTAL} (was ${LASTPASS})"
    log "$MSG"
    if ! tg_send "$MSG"; then log "WARN: telegram send failed"; fi
    if [ "$STORE" = 1 ]; then write_tally "$TODAY" "$NEWTOTAL" "$NEWPASS"; fi
  elif [ "$NEWPASS" -gt "$LASTPASS" ]; then
    MSG="JURIS EVAL improved: ${NEWPASS}/${NEWTOTAL}"
    log "$MSG"
    if ! tg_send "$MSG"; then log "WARN: telegram send failed"; fi
    if [ "$STORE" = 1 ]; then write_tally "$TODAY" "$NEWTOTAL" "$NEWPASS"; fi
  else
    log "stable: ${NEWPASS}/${NEWTOTAL} (was ${LASTPASS}/${LASTTOTAL}) — log only"
    if [ "$STORE" = 1 ]; then write_tally "$TODAY" "$NEWTOTAL" "$NEWPASS"; fi
  fi
  return 0
}

# ---------- main ----------

case "$DRYRUN" in
  1|stable|regress|improve) ;;
  *) log "invalid JURIS_EVAL_DRYRUN value '$DRYRUN' (1|stable|regress|improve)"; exit 2 ;;
esac

# shellcheck disable=SC2046
read -r LASTPASS LASTTOTAL LASTDATE <<< "$(read_tally)"
log "last stored tally: ${LASTPASS}/${LASTTOTAL} (${LASTDATE:-none}) dryrun=${DRYRUN}"

if [ "$DRYRUN" != "0" ]; then
  STORE=0
  NEWTOTAL="$LASTTOTAL"
  case "$DRYRUN" in
    1|stable)  NEWPASS="$LASTPASS" ;;
    regress)   NEWPASS=$(( LASTPASS - 6 )); [ "$NEWPASS" -lt 0 ] && NEWPASS=0 ;;
    improve)   NEWPASS=$(( LASTPASS + 1 )) ;;
  esac
  log "DRYRUN: replaying simulated tally ${NEWPASS}/${NEWTOTAL} through the alert logic (runner skipped, last.json untouched)"
  eval_alert_logic
  log "DRYRUN complete"
  exit 0
fi

# Real run: warm first (documented — the daily eval timer warms before eval),
# then the fast-tier golden run.
log "warm-up start (15 everyday-law questions, real AI)"
if ! "$APP/.venv/bin/python" "$APP/scripts/juris_warm_cache.py" >> "$LOG" 2>&1; then
  log "WARN: cache warm-up failed — continuing with eval"
fi
log "warm-up done; fast-tier runner start"
cd "$APP" || exit 2
"$APP/.venv/bin/python" tests/juris_golden/runner.py --phase legal --limit-per-cat 2 >> "$LOG" 2>&1
RC=$?
log "runner finished rc=$RC"

extract_tally
if [ -z "${NEWPASS:-}" ] || [ "$NEWPASS" = "" ]; then
  MSG="JURIS EVAL FAILED: runner rc=$RC, no tally extracted — see $LOG"
  log "$MSG"
  tg_send "$MSG" || log "WARN: telegram send failed"
  exit 1
fi

STORE=1
log "new tally: ${NEWPASS}/${NEWTOTAL}"
eval_alert_logic
log "eval complete (tally stored in $LAST)"
exit 0
