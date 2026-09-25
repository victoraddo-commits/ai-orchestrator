"""Sollatek UPS reader (Proxmox B) — authoritative power telemetry for KAI.

The UPS is NUT-managed on Proxmox B (`nutdrv_qx`, device 0665:5161) and read
over the existing key-based SSH chain. This module never fabricates a field: any
value NUT does not report is omitted, and a communication failure is surfaced as
``reachable: false`` — never as stale/green.

Mapping: NUT ``ups.status`` flags -> the directive's normalized UPS states.
"""
from __future__ import annotations

import os
import subprocess

NUT_KEY = os.environ.get("KAI_UPS_SSH_KEY", "/root/.ssh/kai_pve_usage")
PVE_B = os.environ.get("KAI_UPS_PVE_B", "root@192.168.1.110")
UPS_NAME = os.environ.get("KAI_UPS_NAME", "sollatek")
SSH_TIMEOUT = int(os.environ.get("KAI_UPS_TIMEOUT", "15"))

_FIELDS = (
    "ups.status", "input.voltage", "output.voltage", "output.frequency",
    "battery.charge", "battery.voltage", "ups.load", "ups.temperature",
    "battery.runtime", "ups.firmware.aux", "ups.type", "ups.beeper.status",
)

# NUT status flag -> normalized state (a UPS can report several flags at once;
# the most severe wins). Order matters: later entries override earlier.
_FLAG_STATE = [
    ("OL", "ONLINE"),
    ("OB", "ON BATTERY"),
    ("LB", "LOW BATTERY"),
    ("RB", "CHARGING"),
    ("CHRG", "CHARGING"),
    ("CAL", "CALIBRATING"),
    ("OFF", "SHUTDOWN"),
    ("OVER", "OVERLOAD"),
    ("TRIM", "VOLTAGE TRIM"),
    ("BOOST", "VOLTAGE BOOST"),
    ("FSD", "SHUTDOWN PENDING"),
    ("ALARM", "ALARM"),
]


def _normalize(status: str) -> list[str]:
    flags = set((status or "").split())
    states = [name for flag, name in _FLAG_STATE if flag in flags]
    # Deduplicate while preserving order, keep highest severity (last).
    seen, out = set(), []
    for s in states:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _query() -> dict:
    # `upsc <name>` with no variable args returns every variable reliably;
    # passing specific names triggers upsc's multi-device mode and returns only
    # the first. Read the full list and filter here.
    cmd = ["ssh", "-i", NUT_KEY, "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"ConnectTimeout={SSH_TIMEOUT}", PVE_B,
           f"upsc {UPS_NAME}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=SSH_TIMEOUT + 5)
    except subprocess.TimeoutExpired:
        return {"reachable": False, "error": "timeout"}
    if proc.returncode != 0:
        return {"reachable": False,
                "error": (proc.stderr or "upsc failed").strip()[:200]}
    data = {}
    for line in proc.stdout.splitlines():
        if ":" in line and not line.startswith("Init SSL"):
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    if not data:
        return {"reachable": False, "error": "no data"}
    return {"reachable": True, "raw": data}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def status() -> dict:
    """Normalized UPS snapshot for the Command Center (no fabrication)."""
    q = _query()
    if not q.get("reachable"):
        return {"reachable": False, "error": q.get("error"),
                "states": ["COMMUNICATION LOST"], "device": "Sollatek Voltsure Ultima LCD 1000"}
    r = q["raw"]
    states = _normalize(r.get("ups.status", ""))
    return {
        "reachable": True,
        "device": "Sollatek Voltsure Ultima LCD 1000",
        "location": "Proxmox B (192.168.1.110)",
        "driver": "nutdrv_qx (Voltronic-QS-Hex)",
        "states": states or ["UNKNOWN"],
        "status_flags": r.get("ups.status", ""),
        "input_voltage": _num(r.get("input.voltage")),
        "output_voltage": _num(r.get("output.voltage")),
        "output_frequency": _num(r.get("output.frequency")),
        "battery_charge_pct": _num(r.get("battery.charge")),
        "battery_voltage": _num(r.get("battery.voltage")),
        "load_pct": _num(r.get("ups.load")),
        "temperature_c": _num(r.get("ups.temperature")),
        "runtime_s": _num(r.get("battery.runtime")),
        "firmware": r.get("ups.firmware.aux"),
        "ups_type": r.get("ups.type"),
        "beeper": r.get("ups.beeper.status"),
    }
