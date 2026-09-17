"""PHASE 7 — autonomous money cycle.

Drives the Arbitra economic brain on the orchestrator's schedule: runs the
Arbitra cycle (discover -> analyze -> review -> measure) and pushes a compact
digest to Telegram. Advisory only: Arbitra records and recommends; real capital
stays in the money-center control plane. Fail-safe by design — a failure here
must never break the orchestrator cycle.
"""
from __future__ import annotations

import json
import os
import time
from urllib import request as _urlreq

ARBITRA_URL = os.environ.get("ARBITRA_URL", "http://192.168.1.118:8096")
TOKEN_FILE = os.environ.get("ARBITRA_TOKEN_FILE", "/etc/kai/arbitra_token")
INTERVAL = float(os.environ.get("KAI_MONEY_CYCLE_INTERVAL", "900"))

_last: float = 0.0


def _token() -> str:
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body or {}).encode()
    req = _urlreq.Request(
        ARBITRA_URL + path,
        data=data,
        method="POST",
        headers={"content-type": "application/json",
                 "authorization": f"Bearer {_token()}"},
    )
    with _urlreq.urlopen(req, timeout=25) as r:
        return json.load(r)


def _notify(text: str) -> None:
    try:
        from core.telegram_bridge import send_telegram_alert
        send_telegram_alert(text)
    except Exception:
        pass


def run_money_cycle(force: bool = False):
    """Run one Arbitra cycle, throttled to INTERVAL. Returns the report or None."""
    global _last
    now = time.time()
    if not force and now - _last < INTERVAL:
        return None
    _last = now
    try:
        out = _post("/arbitra/cycle", {"actor": "scheduler"})
    except Exception as e:  # noqa: BLE001 - never propagate
        return {"error": f"{type(e).__name__}: {e}"}
    cyc = out.get("cycle", {}) if isinstance(out, dict) else {}
    p = cyc.get("portfolio", {}) or {}
    top = cyc.get("top_recommendation") or {}
    net = p.get("net_profit", p.get("margin", "?"))
    digest = cyc.get("ai_digest") or {}
    text = (
        "KAI money cycle\n"
        f"opportunities: {cyc.get('opportunities', '?')} (scored {cyc.get('scored', 0)})\n"
        f"winners/losers: {cyc.get('winners', '?')}/{cyc.get('losers', '?')}\n"
        f"net profit: {net}\n"
        f"top: {top.get('name') or top.get('title') or '-'}"
    )
    if digest.get("text"):
        text += f"\n\nAI ({digest.get('source')}):\n{digest['text']}"
    _notify(text)
    return cyc
