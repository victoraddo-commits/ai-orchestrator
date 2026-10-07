"""Host memory pressure guard (PVE-B freeze post-mortem, 2026-09-26).

The 62Gi PVE-B host ran two 32G model VMs plus 12 LXCs. Starting VM104
at 15:33 HDT filled the last free pages, the kernel OOM-killer killed kvm
twice, the restart loop re-triggered global OOM, and memory thrash froze
sshd/tailscaled/pvedaemon until a hard power cycle.

This module encodes the two rules learned from that incident:

- classify host memory pressure (severity escalates to critical at 95%);
- refuse to start a guest unless free RAM covers guest size plus 4GiB
  headroom for host services (PVE, tailscale, ceph/journal caches).
"""

GIB = 1024 * 1024 * 1024
HEADROOM_GIB = 4


def classify_pressure(total_bytes, used_bytes, swap_used_bytes=0):
    """Return a pressure snapshot: {used_pct, severity, message}.

    Severity: ok < 90, warning >= 90, critical >= 95 (swap in use cannot
    downgrade an already-critical node, it is appended to the message).
    """
    total = max(int(total_bytes or 0), 1)
    used = max(int(used_bytes or 0), 0)
    used_pct = round(used / total * 100, 1)

    if used_pct >= 95:
        severity = "critical"
    elif used_pct >= 90:
        severity = "warning"
    else:
        severity = "ok"

    message = f"Host memory at {used_pct}%"
    if swap_used_bytes and int(swap_used_bytes) > 0:
        message += f" (swap in use: {round(int(swap_used_bytes) / GIB, 1)}GiB)"
    if severity != "ok":
        message += " — do not start guests; investigate memory holders"

    return {"used_pct": used_pct, "severity": severity, "message": message}


def can_start_guest(total_bytes, used_bytes, guest_bytes, headroom_bytes=HEADROOM_GIB * GIB):
    """Decide whether a guest of guest_bytes may start on this host.

    Returns (allowed, reason). Allowed only when free RAM
    (total - used) covers guest size plus headroom for host services.
    """
    total = max(int(total_bytes or 0), 1)
    used = max(int(used_bytes or 0), 0)
    guest = max(int(guest_bytes or 0), 0)

    free = total - used
    required = guest + headroom_bytes
    if free >= required:
        return True, "ok"
    return False, (
        f"insufficient memory: {round(free / GIB, 1)}GiB free < guest "
        f"{round(guest / GIB, 1)}GiB + {round(headroom_bytes / GIB, 1)}GiB headroom"
    )
