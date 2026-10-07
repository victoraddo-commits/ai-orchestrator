#!/usr/bin/env python3
"""R2 online monitor — watches for the Spectrum site (R2) coming online.

Detects R2 via either signal:
  * WireGuard: the R2 peer on the exit-node wg0 has a handshake (since != "never")
  * Tailscale: peer 100.64.0.3 is Online on the deerude net

On an OFFLINE -> ONLINE transition it logs, writes state, and POSTs a
notification to the Command Center ``/notify`` endpoint (which also raises a
Telegram alert for warn/critical). On ONLINE -> OFFLINE it logs the drop.

Data source: the kaidash dashboard JSON, reachable from the CC runner through
the PVE-B socat bridge (``http://192.168.1.110:51880/api``).
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request

DASH_URL = os.environ.get("R2_DASH_URL", "http://192.168.1.110:51880/api")
NOTIFY_URL = os.environ.get("R2_NOTIFY_URL", "https://127.0.0.1:8000/notify")
NOTIFY_TOKEN = os.environ.get("R2_NOTIFY_TOKEN", "")
POLL_SECS = int(os.environ.get("R2_POLL_SECS", "20"))
STATE_FILE = os.environ.get("R2_STATE_FILE", "/var/lib/kai/r2-monitor.json")
LOG_FILE = os.environ.get("R2_LOG_FILE", "/var/log/kai-r2-monitor.log")
WG_PUB = "4ZgMh2jehWyNXlaah09l5ikPb9gjCCvbvEV8aiJJAl4="
TS_IP = "100.64.0.3"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _get_json(url: str, timeout: int = 8) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def probe() -> dict:
    """Return {online, wg_up, ts_up, detail, rx_mb, tx_mb, ts_last_seen}."""
    out = {"online": False, "wg_up": False, "ts_up": False, "detail": "",
           "rx_mb": 0.0, "tx_mb": 0.0, "ts_last_seen": ""}
    try:
        d = _get_json(DASH_URL)
    except Exception as e:  # noqa: BLE001
        out["detail"] = f"dashboard unreachable: {type(e).__name__}"
        return out
    for f in d.get("ifaces", []):
        for pe in f.get("peers", []):
            if pe.get("pub") == WG_PUB:
                out["wg_up"] = pe.get("since") != "never"
                out["rx_mb"] = float(pe.get("rx_mb") or 0)
                out["tx_mb"] = float(pe.get("tx_mb") or 0)
    ts = d.get("tailscale", {})
    for p in ts.get("peers", []):
        if p.get("ip") == TS_IP:
            out["ts_up"] = bool(p.get("online"))
            out["ts_last_seen"] = p.get("last_seen") or ""
    out["online"] = out["wg_up"] or out["ts_up"]
    out["detail"] = f"wg_up={out['wg_up']} ts_up={out['ts_up']}"
    return out


def notify(title: str, message: str, severity: str) -> None:
    """Publish via the orchestrator NotificationManager (history + Telegram),
    falling back to the HTTP /notify endpoint if the import is unavailable."""
    if not os.environ.get("R2_FORCE_HTTP_NOTIFY"):
        try:
            from core.notifications import NotificationManager
            sev = {"warn": "critical", "info": "informational"}.get(severity, "informational")
            rec = NotificationManager.enqueue(severity=sev, title=title, body=message,
                                              source="r2_monitor", module="system")
            log(f"enqueue -> {'ok ' + rec['id'] if rec else 'deduped'}")
            return
        except Exception as e:  # noqa: BLE001
            log(f"enqueue unavailable ({type(e).__name__}: {e}); falling back to /notify")
    body = json.dumps({"source": "r2-monitor", "severity": severity,
                       "title": title, "message": message}).encode()
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + (NOTIFY_TOKEN or "kai-r2-monitor")}
    req = urllib.request.Request(NOTIFY_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8, context=_CTX) as r:
            log(f"notify -> HTTP {r.status}")
    except Exception as e:  # noqa: BLE001
        log(f"notify failed: {type(e).__name__}: {e}")


def load_state() -> dict:
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"state": "unknown", "since": 0, "last_check": 0, "first_online": 0}


def save_state(st: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log(f"state write failed: {e}")


def main() -> int:
    log(f"r2-monitor starting (poll {POLL_SECS}s, dash {DASH_URL})")
    st = load_state()
    while True:
        pr = probe()
        now = int(time.time())
        new = "online" if pr["online"] else "offline"
        prev = st.get("state", "unknown")
        st["last_check"] = now
        st["wg_up"] = pr["wg_up"]
        st["ts_up"] = pr["ts_up"]
        st["detail"] = pr["detail"]
        st["rx_mb"] = pr["rx_mb"]
        st["tx_mb"] = pr["tx_mb"]
        st["ts_last_seen"] = pr["ts_last_seen"]
        if new != prev:
            st["state"] = new
            st["since"] = now
            if new == "online":
                st["first_online"] = st.get("first_online") or now
                log(f"R2 ONLINE (prev={prev}) {pr['detail']}")
                notify(
                    "R2 is ONLINE",
                    f"R2 (spectrum) came online at {time.strftime('%Y-%m-%d %H:%M:%S')} — "
                    f"{pr['detail']}. Safe to proceed with WireGuard deployment.",
                    "warn",
                )
            else:
                log(f"R2 OFFLINE (prev={prev}) last_seen={pr['ts_last_seen']} {pr['detail']}")
                if prev == "online":
                    notify(
                        "R2 went OFFLINE",
                        f"R2 (spectrum) dropped at {time.strftime('%Y-%m-%d %H:%M:%S')} — "
                        f"last seen {pr['ts_last_seen']}.",
                        "info",
                    )
        save_state(st)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
