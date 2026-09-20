"""Resolve the 7 remaining open incidents (2026-09-20) once their causes are
fixed and evidenced. Each resolution note carries the evidence.

Backlog (from memory/incidents.json):
  2725bce5 proxmox-backup  "No recent backup (vzdump) history found"
  263ca7f9 network (info)  "New node discovered: pve"
  8af054a0 network (info)  "New node discovered: Z Fold"
  6d8571bb network (crit)  "Tailscale peer pve went offline"
  eafe7495 network (info)  "Tailscale peer pve came online"
  c66abce4 telegram        "Telegram reminder delivery failing"
  e328933c proxmox-network "Network interfaces present but inactive: [...]"
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/ai-orchestrator")

from core import incident_manager as im

NOW = datetime.now(timezone.utc)

RESOLUTIONS = [
    (
        "proxmox-backup",
        "No recent backup (vzdump) history found",
        "False positive. The check only scanned the node's recent 50-task "
        "window (all push_file); vzdump jobs scroll out of it. Backups do run: "
        "the ?typefilter=vzdump task query returns 7 vzdump jobs (newest "
        "2026-09-18 12:30 UTC, status OK) and /mnt/kai-c/dump holds daily "
        "vzdump-* dumps. Fixed in ad6895d: backup health now sources the "
        "vzdump-filtered task list + backup storage listing.",
    ),
    (
        "telegram",
        "Telegram reminder delivery failing",
        "Fixed in 4bb6ee5 and verified live 2026-09-20. Root cause: the AI-5 "
        "stale-failure reminder embedded a 5,843-char failure_reason, exceeding "
        "Telegram's 4096 cap -> HTTP 400 'message is too long' on every cycle. "
        "send_message() now truncates to the UTF-16 limit. getMe ok "
        "(@KaiEnzo_bot); the exact 6,021-char AI-5 reminder now delivers "
        "(message_id 17018); short sends still 200.",
    ),
    (
        "proxmox-network",
        "Network interfaces present but inactive: ['nic1', 'nic2', 'nic3']",
        "Not actionable: nic1/nic2/nic3 are intentionally unused on PVE-B -- "
        "'iface nicN inet manual' in /etc/network/interfaces, not bridged/"
        "bonded and wired to no VM or CT (verified via ip -br link, "
        "bridge-ports and pct/qm configs). Fixed in ad6895d: "
        "KNOWN_UNUSED_INTERFACES allowlist; unknown inactive NICs still alert.",
    ),
    (
        "network",
        "Tailscale peer pve went offline",
        "Transient/obsolete. The paired 'came online' event (eafe7495) and a "
        "live `tailscale status` (2026-09-20) both show proxmox-b/pve active. "
        "The 3-week-old offline event is no longer a present problem.",
    ),
]


def main():
    total = 0
    for service, issue, note in RESOLUTIONS:
        n = im.resolve_incidents(service, issue, note, now=NOW)
        print(f"resolve_incidents({service!r}, {issue[:40]!r}...) -> {n}")
        total += n

    n = im.resolve_informational_network_incidents(
        "Auto-resolved: informational discovery/online event (observed, no "
        "action needed). New events now auto-resolve at creation (5161343).",
        now=NOW,
    )
    print(f"resolve_informational_network_incidents -> {n}")
    total += n

    remaining = im.get_active_incidents()
    print(f"total resolved this run: {total}")
    print(f"remaining open: {len(remaining)}")
    for i in remaining:
        print("  OPEN:", i.get("service"), i.get("severity"),
              (i.get("issue") or "")[:70])


if __name__ == "__main__":
    main()
