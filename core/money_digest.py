"""Kai Money digest — periodic treasury + arbitra progress to Telegram.

Runs on CT111. Pulls the money-center + arbitra APIs, computes deltas vs the
previous snapshot, and sends a concise digest to the owner chat.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MC = os.environ.get("MONEY_CENTER_URL", "http://192.168.1.118:8095")
ARB = os.environ.get("ARBITRA_URL", "http://192.168.1.118:8096")
TOKEN_FILE = os.environ.get("MONEY_USER_TOKEN_FILE", "/root/.credentials/money-user-token")
SNAP = os.environ.get("MONEY_DIGEST_SNAPSHOT", "/opt/ai-orchestrator/memory/money_digest.json")


def _token() -> str:
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def get(url: str, tok: str = "") -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"} if tok else {})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def fmt(n: float) -> str:
    return f"{n:,.2f}"


def build_message() -> str:
    tok = _token()
    t = get(f"{MC}/treasury/summary", tok)
    rec = get(f"{MC}/reconciliation", tok)
    pos = get(f"{MC}/kai/position", tok)
    st = get(f"{ARB}/arbitra/status")
    port = get(f"{ARB}/arbitra/portfolio")
    perf = get(f"{ARB}/arbitra/performance")

    master = num(((t.get("master") or {}) if isinstance(t, dict) else {}).get("balance"))
    avail = num(t.get("available_for_allocation") if isinstance(t, dict) else 0)
    deployed = num(t.get("deployed_in_operations") if isinstance(t, dict) else 0)
    total_cap = num(t.get("total_ecosystem_capital") if isinstance(t, dict) else 0)

    prev = {}
    try:
        with open(SNAP) as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = {}

    d_bal = master - num(prev.get("master"))
    d_profit = num(perf.get("net_profit")) - num(prev.get("net_profit"))
    d_div = num(perf.get("dividends_paid")) - num(prev.get("dividends_paid"))

    checks = rec.get("checks", []) if isinstance(rec, dict) else []
    ok = sum(1 for c in checks if c.get("ok"))

    lines = [f"💰 *Money Digest* — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    lines.append(f"🏦 Treasury: *{fmt(master)} USDT*  ({'+' if d_bal >= 0 else ''}{fmt(d_bal)} vs last)")
    lines.append(f"    available {fmt(avail)} · deployed {fmt(deployed)} · ecosystem {fmt(total_cap)}")

    if "error" not in st:
        lines.append(f"📊 Arbitra: {st.get('ventures', 0)} ventures "
                     f"({st.get('active_ventures', 0)} active) · {st.get('opportunities', 0)} opportunities")
    ops = t.get("operations", []) if isinstance(t, dict) else []
    if ops:
        lines.append("⚙️ Engines (allocated · P&L):")
        for o in ops:
            bal = num((o.get("treasury_balance") or {}).get("balance"))
            pnl = num((o.get("pnl") or {}).get("pnl"))
            lines.append(f"    {o.get('slug')}: {fmt(bal)} · {'+' if pnl >= 0 else ''}{fmt(pnl)} · {o.get('health', '?')}")
    if "error" not in perf:
        lines.append(f"    revenue {fmt(num(perf.get('revenue')))} · expenses {fmt(num(perf.get('expenses')))} "
                     f"· net *{fmt(num(perf.get('net_profit')))}*  ({'+' if d_profit >= 0 else ''}{fmt(d_profit)})")
        lines.append(f"    dividends paid {fmt(num(perf.get('dividends_paid')))} "
                     f"({'+' if d_div >= 0 else ''}{fmt(d_div)}) · ROI {num(perf.get('roi_percent')):.2f}%")
    if isinstance(pos, dict) and "error" not in pos:
        lines.append(f"📈 Position: accrued {fmt(num(pos.get('total_accrued')))} · "
                     f"distributed {fmt(num(pos.get('total_distributed_cash')))}")

    if checks:
        badge = "✅" if ok == len(checks) else "⚠️"
        lines.append(f"{badge} Reconciliation: {ok}/{len(checks)} checks OK")

    moved = any(abs(x) > 1e-9 for x in (d_bal, d_profit, d_div))
    lines.append("")
    lines.append("📌 " + ("Progress since last update." if moved else "No change since last update."))

    with open(SNAP, "w") as fh:
        json.dump({
            "master": master,
            "net_profit": num(perf.get("net_profit")) if isinstance(perf, dict) else 0,
            "dividends_paid": num(perf.get("dividends_paid")) if isinstance(perf, dict) else 0,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, fh)
    return "\n".join(lines)


def send(text: str) -> dict:
    tok = os.environ.get("KAI_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("KAI_TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        return {"ok": False, "error": "telegram env missing"}
    data = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    msg = build_message()
    print(msg)
    print("--- sent:", send(msg).get("ok"))
