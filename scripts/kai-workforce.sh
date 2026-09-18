#!/usr/bin/env bash
# kai-workforce — OpenCode/CLI wrapper for the KAI Teammate Factory API.
#
# Auth: the same bridge token the CloudCLI/OpenCode bridge uses.
#   KAI_API_TOKEN      explicit token (highest priority)
#   KAI_API_TOKEN_FILE token file (default ~/.ai-orchestrator/api_token)
#   KAI_API_BASE       API base URL (default https://127.0.0.1:8000)
#
# Usage:
#   scripts/kai-workforce.sh teammate-create <role> [skill ...]
#   scripts/kai-workforce.sh teammates
#   scripts/kai-workforce.sh teammate <id>
#   scripts/kai-workforce.sh team-create "<requirement>"
#   scripts/kai-workforce.sh teams
#   scripts/kai-workforce.sh team <id>
#   scripts/kai-workforce.sh mission "<goal>"
#   scripts/kai-workforce.sh missions
#   scripts/kai-workforce.sh mission-get <id>
set -euo pipefail

BASE="${KAI_API_BASE:-https://127.0.0.1:8000}"
TOKEN="${KAI_API_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  for f in "${KAI_API_TOKEN_FILE:-}" "$HOME/.ai-orchestrator/api_token" /root/.ai-orchestrator/api_token; do
    if [ -n "$f" ] && [ -f "$f" ]; then TOKEN="$(tr -d '\n' < "$f")"; break; fi
  done
fi
if [ -z "$TOKEN" ]; then
  echo "kai-workforce: no token (set KAI_API_TOKEN or KAI_API_TOKEN_FILE)" >&2
  exit 1
fi

_json() { # _json '<python expr over d>' ; reads JSON on stdin, prints field
  python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null || cat
}

cmd="${1:-}"; shift || true
case "$cmd" in
  teammate-create)
    role="${1:-coder}"; shift || true
    python3 - "$role" "$@" <<'PY' > /tmp/kai_workforce_body.$$
import json, sys
print(json.dumps({"role": sys.argv[1], "skills": sys.argv[2:] or None}))
PY
    curl -sk -X POST "$BASE/api/workforce/teammates" \
      -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
      --data-binary "@/tmp/kai_workforce_body.$$"; echo
    rm -f /tmp/kai_workforce_body.$$
    ;;
  teammates)
    curl -sk "$BASE/api/workforce/teammates" -H "Authorization: Bearer $TOKEN"; echo ;;
  teammate)
    curl -sk "$BASE/api/workforce/teammates/$1" -H "Authorization: Bearer $TOKEN"; echo ;;
  team-create)
    python3 - "$1" <<'PY' > /tmp/kai_workforce_body.$$
import json, sys
print(json.dumps({"requirement": sys.argv[1]}))
PY
    curl -sk -X POST "$BASE/api/workforce/teams" \
      -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
      --data-binary "@/tmp/kai_workforce_body.$$"; echo
    rm -f /tmp/kai_workforce_body.$$
    ;;
  teams)
    curl -sk "$BASE/api/workforce/teams" -H "Authorization: Bearer $TOKEN"; echo ;;
  team)
    curl -sk "$BASE/api/workforce/teams/$1" -H "Authorization: Bearer $TOKEN"; echo ;;
  mission)
    python3 - "$1" <<'PY' > /tmp/kai_workforce_body.$$
import json, sys
print(json.dumps({"goal": sys.argv[1]}))
PY
    curl -sk -X POST "$BASE/api/missions" \
      -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
      --data-binary "@/tmp/kai_workforce_body.$$"; echo
    rm -f /tmp/kai_workforce_body.$$
    ;;
  missions)
    curl -sk "$BASE/api/missions" -H "Authorization: Bearer $TOKEN"; echo ;;
  mission-get)
    curl -sk "$BASE/api/missions/$1" -H "Authorization: Bearer $TOKEN"; echo ;;
  *)
    sed -n '2,20p' "$0"; exit 2 ;;
esac
