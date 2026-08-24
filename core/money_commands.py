"""Telegram command layer for the KAI Money Ecosystem.

Lets the operator approve/reject capital requests, inspect treasuries and
balances, and trigger payouts directly from the Telegram chat — no dashboard
required. Called from telegram_bridge.route_inbound_reply() BEFORE build
routing, so money commands always win over the software-factory approval
matcher (a bare "approve" still routes to pending builds; money approvals
are explicit: "approve 3" with /pending context or "cr approve 3").

Auth: money-center bearer token with role 'user' (money-user-token file).
The token never leaves this process; only HTTPS/LAN calls carry it.

Commands (case-insensitive):
  /pending                  — list PENDING capital requests
  /treasury                 — master + per-operation balances
  /ops                      — operation status table
  cr approve <id> [amount]  — approve request id (optionally partially)
  cr reject <id>            — reject request id
  pay <op> <amount> <note>  — record an external payout/deposit note
"""

import json
import os
import re
from urllib import request as _urlreq
from urllib.error import URLError, HTTPError

MONEY_CENTER_URL = os.environ.get("MONEY_CENTER_URL", "http://127.0.0.1:8000")
# The orchestrator proxies nothing here — money-center lives on CT108.
_DEFAULT_MC = "http://192.168.1.118:8095"
USER_TOKEN_FILE = os.environ.get("MONEY_USER_TOKEN_FILE", "/root/.credentials/money-user-token")

_TIMEOUT = 10


def _token():
    try:
        with open(USER_TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _api(method, path, body=None):
    base = os.environ.get("KAI_MONEY_URL", _DEFAULT_MC)
    req = _urlreq.Request(
        f"{base}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "authorization": f"Bearer {_token()}",
            "content-type": "application/json",
        },
    )
    try:
        with _urlreq.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except (URLError, TimeoutError) as e:
        return 0, {"error": str(e)}


def _fmt_usd(n):
    n = float(n or 0)
    return f"${n:,.2f}" if abs(n) >= 1 else f"${n:.4f}"


def cmd_pending():
    code, reqs = _api("GET", "/capital-requests?status=pending")
    if code == 0:
        return f"⚠️ Money Center unreachable: {reqs.get('error', 'network')}"
    if not reqs:
        return "✅ No pending capital requests."
    lines = ["📋 Pending capital requests:"]
    for r in reqs:
        lines.append(
            f"\n#{r['id']} · {r.get('operation_slug', '?')} asks {_fmt_usd(r.get('amount'))}"
            f"\n   \"{(r.get('reason') or '')[:140]}\""
            f"\n   → reply: cr approve {r['id']}  |  cr reject {r['id']}"
        )
    return "\n".join(lines)


def cmd_treasury():
    code, d = _api("GET", "/treasury/summary")
    if code == 0:
        return f"⚠️ Money Center unreachable: {d.get('error', 'network')}"
    m = d.get("master", {})
    lines = [
        "🏦 Treasuries:",
        f"  Master reserve: {_fmt_usd(m.get('balance'))} "
        f"(funded {_fmt_usd(m.get('funded'))}, earned {_fmt_usd(m.get('earned'))})",
        f"  Ecosystem total: {_fmt_usd(d.get('total_ecosystem_capital'))}",
        "",
        "Operations:",
    ]
    for o in d.get("operations", []):
        tb = o.get("treasury_balance") or {}
        lines.append(
            f"  {o.get('slug', '?'):14} {o.get('status', '?'):9} "
            f"alloc {_fmt_usd(o.get('allocated'))} · pnl {_fmt_usd((o.get('pnl') or {}).get('pnl'))}"
        )
    return "\n".join(lines)


def cmd_ops():
    code, d = _api("GET", "/operations")
    if code == 0:
        return f"⚠️ Money Center unreachable: {d.get('error', 'network')}"
    ops = d if isinstance(d, list) else d.get("operations", [])
    lines = ["⚙️ Operations:"]
    for o in ops:
        lines.append(f"  {o.get('slug', '?'):14} {o.get('status', '?'):9} health {o.get('health', '?')}")
    return "\n".join(lines)


def cmd_cr_approve(args):
    if not args:
        return "Usage: cr approve <id> [partial_amount]"
    parts = args.split()
    rid = parts[0]
    amount = None
    if len(parts) > 1:
        try:
            amount = float(parts[1])
        except ValueError:
            return f"'{parts[1]}' is not an amount."
    body = {"decision": "approve_partial" if amount else "approve"}
    if amount:
        # decide route reads body.amount for partial approvals
        body["amount"] = amount
    code, resp = _api("POST", f"/capital-requests/{rid}/decide", body)
    if code == 0:
        return f"⚠️ Money Center unreachable: {resp.get('error', 'network')}"
    if code >= 300:
        return f"❌ Approve failed: {resp.get('error', code)}"
    # Approval alone doesn't move money — execute moves master → operation.
    code2, ex = _api("POST", f"/capital-requests/{rid}/execute")
    if code2 >= 300:
        return (
            f"✅ Approved #{rid} ({_fmt_usd(resp.get('approved_amount'))}), "
            f"but execution failed: {ex.get('error', code2)}\n"
            f"Retry later with: cr exec {rid}"
        )
    bal = ex.get("treasury_balance") or {}
    return (
        f"✅ Approved + executed #{rid}: {_fmt_usd(ex.get('amount'))} "
        f"moved to {resp.get('operation_slug', 'operation')}. "
        f"Op treasury now {_fmt_usd(bal.get('balance'))}."
    )


def cmd_cr_exec(args):
    rid = (args.split() or [""])[0]
    if not rid:
        return "Usage: cr exec <id>"
    code, ex = _api("POST", f"/capital-requests/{rid}/execute")
    if code == 0:
        return f"⚠️ Money Center unreachable: {ex.get('error', 'network')}"
    if code >= 300:
        return f"❌ Execute failed: {ex.get('error', code)}"
    bal = ex.get("treasury_balance") or {}
    return f"✅ Executed #{rid}: {_fmt_usd(ex.get('amount'))}. Op treasury now {_fmt_usd(bal.get('balance'))}."


def cmd_cr_reject(args):
    rid = (args.split() or [""])[0]
    if not rid:
        return "Usage: cr reject <id>"
    code, resp = _api("POST", f"/capital-requests/{rid}/decide", {"decision": "reject"})
    if code == 0:
        return f"⚠️ Money Center unreachable: {resp.get('error', 'network')}"
    if code >= 300:
        return f"❌ Reject failed: {resp.get('error', code)}"
    return f"🚫 Rejected #{rid}."


_MONEY_RE = re.compile(
    r"^/?(?:money\s+)?(?P<cmd>pending|treasury|ops|cr|pay|wallets|payouts|risk|menu)\b(?P<rest>.*)$",
    re.IGNORECASE,
)

_MENU = (
    "🤖 KAI bot — command menu\n"
    "\n"
    "💰 Money:\n"
    "/pending — capital requests awaiting your approval\n"
    "/treasury — balances + pnl overview\n"
    "/ops — operation status table\n"
    "/risk — recent risk events\n"
    "cr approve <id> [amt] — approve (+ execute)\n"
    "cr approve <id> 0.5 — partial approval\n"
    "cr reject <id> — reject request\n"
    "cr exec <id> — execute an already-approved request\n"
    "\n"
    "👛 Payouts & wallets:\n"
    "/wallets — your registered withdrawal wallets + funding address\n"
    "/payouts — recent payouts + status\n"
    "\n"
    "Everything else: just chat with Kai normally."
)


def cmd_wallets():
    code, d = _api("GET", "/wallets")
    if code == 0:
        return f"⚠️ Money Center unreachable: {d.get('error', 'network')}"
    lines = ["👛 Registered withdrawal wallets:"]
    for w in d.get("wallets", []):
        state = "DISABLED" if w.get("disabled") else "active"
        lines.append(f"  #{w['id']} {w.get('label')} ({w.get('chain')}) [{state}]\n     {w.get('address')}")
    f = d.get("funding") or {}
    evm = ((f.get("addresses") or {}).get("evm") or {})
    if evm.get("address"):
        lines.append("\n📥 Funding (send capital here):")
        lines.append(f"  EVM: {evm['address']} ({', '.join(evm.get('assets', []))})")
    if not len(d.get("wallets", [])) and not evm:
        return "No wallets registered yet. Add them in the dashboard Wallet tab."
    return "\n".join(lines)


def cmd_payouts():
    code, d = _api("GET", "/payouts")
    if code == 0:
        return f"⚠️ Money Center unreachable: {d.get('error', 'network')}"
    rows = d.get("payouts", [])
    if not rows:
        return "✅ No payouts yet."
    lines = ["💸 Recent payouts:"]
    for p in rows[:10]:
        lines.append(f"  #{p['id']} {_fmt_usd(p.get('amount'))} → {(p.get('dest_address') or '')[:14]}… [{p.get('status')}] {p.get('from_treasury')}")
    return "\n".join(lines)


def cmd_risk():
    code, d = _api("GET", "/risk/events")
    if code == 0:
        return f"⚠️ Money Center unreachable: {d.get('error', 'network')}"
    rows = d.get("events", d) if isinstance(d, dict) else d
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    open_rows = [r for r in rows if not r.get("resolved_at")]
    if not open_rows:
        return "✅ No unresolved risk events."
    lines = ["⚠️ Unresolved risk events:"]
    for r in open_rows[:10]:
        sev = str(r.get("severity", "?")).upper()
        lines.append(f"  [{sev}] {r.get('kind', '?')} · {r.get('operation_slug', '')}: {str(r.get('detail', ''))[:90]}")
    return "\n".join(lines)


def handle_money_command(text):
    """Return a reply string if `text` is a money command, else None."""
    stripped = text.strip()
    lowered = lowered_case = stripped.lower()
    if lowered in ("/menu", "/start", "/help"):
        return _MENU
    m = _MONEY_RE.match(stripped)
    if not m:
        return None
    cmd = m.group("cmd").lower()
    rest = m.group("rest").strip()
    if cmd == "pending":
        return cmd_pending()
    if cmd == "treasury":
        return cmd_treasury()
    if cmd == "ops":
        return cmd_ops()
    if cmd == "wallets":
        return cmd_wallets()
    if cmd == "payouts":
        return cmd_payouts()
    if cmd == "risk":
        return cmd_risk()
    if cmd == "menu":
        return _MENU
    if cmd == "cr":
        sub = rest.split(None, 1)
        action = sub[0].lower() if sub else ""
        args = sub[1] if len(sub) > 1 else ""
        if action == "approve":
            return cmd_cr_approve(args)
        if action == "reject":
            return cmd_cr_reject(args)
        if action in ("exec", "execute"):
            return cmd_cr_exec(args)
        return cmd_pending()  # bare "cr" shows the actionable list
    return None
