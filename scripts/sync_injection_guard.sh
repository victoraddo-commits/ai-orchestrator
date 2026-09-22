#!/usr/bin/env bash
#
# sync_injection_guard.sh — keep the prompt-injection guard byte-identical
# across every deployment of it:
#
#   * canonical source : <repo>/core/legal/injection.py   (this repo, CT111)
#   * Legal Brain (CT100): /opt/kai-legal-brain/core/legal/injection.py
#   * source repo (CT113): /root/juris-legal-brain/core/legal/injection.py
#
# Usage:
#   scripts/sync_injection_guard.sh            # push + restart + verify
#   scripts/sync_injection_guard.sh --check    # verify only; non-zero on drift
#
# Exit codes: 0 = in sync, 1 = drift / verification failed, 2 = usage / setup.
#
# Transport: the script runs on the orchestrator container (CT111) and reaches
# the CT100/CT113 containers through the Proxmox B host over SSH (default key
# /root/.ssh/kai_pve_usage). Override with PVE_HOST / PVE_KEY. All paths are
# overridable via environment variables so this can be wired into CI or a
# pre-commit hook later (run `--check`).
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${INJECTION_GUARD_SRC:-$REPO_ROOT/core/legal/injection.py}"

PVE_HOST="${PVE_HOST:-192.168.1.110}"
PVE_KEY="${PVE_KEY:-/root/.ssh/kai_pve_usage}"
CT100_ID="${CT100_ID:-100}"
CT113_ID="${CT113_ID:-113}"
CT100_PATH="${CT100_PATH:-/opt/kai-legal-brain/core/legal/injection.py}"
CT113_PATH="${CT113_PATH:-/root/juris-legal-brain/core/legal/injection.py}"
CT100_SERVICE="${CT100_SERVICE:-kai-legal-brain}"

HOST_STAGE="${HOST_STAGE:-/root/.sync_injection_guard.stage.py}"

SSH_OPTS=(-i "$PVE_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no)

usage() { grep '^#' "$0" | sed 's/^# \{0,1\}//'; }

ssh_pve() {
    ssh "${SSH_OPTS[@]}" "root@$PVE_HOST" "$@"
}

local_sha() { sha256sum "$SRC" | awk '{print $1}'; }

remote_sha() {  # <vmid> <path>
    ssh_pve "pct exec $1 -- sha256sum '$2' 2>/dev/null | awk '{print \$1}'" \
        | tr -d '\r' | tail -n1
}

check() {
    local want s100 s113 rc=0
    want="$(local_sha)"
    s100="$(remote_sha "$CT100_ID" "$CT100_PATH" || true)"
    s113="$(remote_sha "$CT113_ID" "$CT113_PATH" || true)"

    printf 'canonical  %-12s %s\n' "local" "$want"
    printf 'CT%-3s      %-12s %s\n' "$CT100_ID" "legal-brain" "${s100:-<missing>}"
    printf 'CT%-3s      %-12s %s\n' "$CT113_ID" "source-repo" "${s113:-<missing>}"

    if [ "$want" != "$s100" ]; then
        echo "DRIFT: CT$CT100_ID ($CT100_PATH) does not match canonical" >&2
        rc=1
    fi
    if [ "$want" != "$s113" ]; then
        echo "DRIFT: CT$CT113_ID ($CT113_PATH) does not match canonical" >&2
        rc=1
    fi
    return $rc
}

sync() {
    if [ ! -f "$SRC" ]; then
        echo "source not found: $SRC" >&2
        exit 2
    fi

    local want s100 s113
    want="$(local_sha)"
    s100="$(remote_sha "$CT100_ID" "$CT100_PATH" || true)"
    s113="$(remote_sha "$CT113_ID" "$CT113_PATH" || true)"

    if [ "$want" = "$s100" ] && [ "$want" = "$s113" ]; then
        echo "already in sync ($want)"
        return 0
    fi

    echo "staging canonical guard on $PVE_HOST ..."
    ssh_pve "cat > '$HOST_STAGE'" < "$SRC"

    echo "pushing to CT$CT100_ID and CT$CT113_ID ..."
    ssh_pve "set -e; pct push $CT100_ID '$HOST_STAGE' '$CT100_PATH' -perms 0644; \
             pct push $CT113_ID '$HOST_STAGE' '$CT113_PATH' -perms 0644; \
             rm -f '$HOST_STAGE'"

    echo "restarting $CT100_SERVICE on CT$CT100_ID ..."
    ssh_pve "pct exec $CT100_ID -- systemctl restart '$CT100_SERVICE'"

    if check; then
        echo "sync OK — all copies match canonical sha256"
    else
        echo "sync FAILED — verification mismatch" >&2
        exit 1
    fi
}

case "${1:-}" in
    ""|--sync) sync ;;
    --check)  check ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
esac
