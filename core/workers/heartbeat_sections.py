"""Comprehensive heartbeat sections for the kai-enzo status digest.

Merges the former airdrop-hunter (akush233bot) heartbeat into the
kai-enzo-bot heartbeat: every section is gathered live at send time from
real system state. Missing data is reported as "unknown" -- never
fabricated. No secrets, tokens, SMS bodies, or personal transaction
amounts appear in the output. Every probe is short-timeout and
exception-isolated so the heartbeat can never crash the digest.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(__file__).resolve().parents[2] / "memory"

SMS_HEALTH_URL = "http://127.0.0.1:8770/health"
AKUSH_HEALTH_URL = "https://192.168.1.118:8095/health"
AKUSH_DETAILED_URL = "https://192.168.1.118:8095/internal/health/detailed"
VAULT_HOST = ("192.168.1.107", 8443)
BRAIN_URL = "http://127.0.0.1:11434/api/tags"

PROBE_TIMEOUT = 3.0

WARN_EMOJI = "⚠️"
OK_EMOJI = "✅"
BAD_EMOJI = "🔴"
UNKNOWN_EMOJI = "❓"


def _fetch_json(url: str, timeout: float = PROBE_TIMEOUT, tls_lan: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "kai-heartbeat/1.0"})
    if url.startswith("https"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if tls_lan:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.load_default_certs()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    else:
        opener = urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _service_section() -> tuple[str, list[str]]:
    parts: list[str] = []
    worst = "ok"
    sms = _try(lambda: _fetch_json(SMS_HEALTH_URL))
    if sms and isinstance(sms.get("worker"), dict):
        w = sms["worker"]
        parts.append(f"sms ok (inbox {w.get('inbox', '?')})")
    else:
        parts.append(f"sms {BAD_EMOJI} unreachable")
        worst = "bad"
    akush = _try(lambda: _fetch_json(AKUSH_HEALTH_URL, tls_lan=True))
    if akush and akush.get("status") == "ok":
        db_field = akush.get("db")
        if isinstance(db_field, dict):
            latency = db_field.get("latency_ms", "?")
        else:
            latency = akush.get("latency_ms", db_field if isinstance(db_field, str) else "?")
        parts.append(f"akush ok ({latency})")
    else:
        parts.append(f"akush {BAD_EMOJI} unreachable")
        worst = "bad"
    vault_ok = _try(_vault_alive)
    if vault_ok is True:
        parts.append("vault ok")
    else:
        parts.append(f"vault {WARN_EMOJI} unreachable")
        worst = worst if worst == "bad" else "degraded"
    return worst, parts


def _vault_alive() -> bool:
    import socket
    with socket.create_connection(VAULT_HOST, timeout=PROBE_TIMEOUT):
        return True


def _money_section(bot_client) -> tuple[str, list[str]]:
    lines: list[str] = []
    worst = "ok"
    detailed = None
    if bot_client is not None:
        raw_base = str(getattr(bot_client, "base", "")).split("/api/v1")[0]
        raw = _try(lambda: type(bot_client)(base=raw_base)) if raw_base else None
        if raw is not None:
            res = _try(lambda: raw.get("/internal/health/detailed"))
            if isinstance(res, tuple) and len(res) == 2 and res[0] == 200:
                detailed = res[1]
            elif isinstance(res, dict):
                detailed = res
    if detailed and isinstance(detailed, dict):
        sched = detailed.get("scheduler") or {}
        fails = detailed.get("pipeline_failures", 0)
        newest = detailed.get("newest_ingest_age_s", detailed.get("newest_ingest_age", "?"))
        lines.append(f"akush scheduler: {sched.get('last_run', 'unknown')}, errors: {sched.get('errored', '?')}")
        lines.append(f"akush pipeline failures: {fails}, newest ingest: {newest}")
        if fails:
            worst = "degraded"
    else:
        lines.append("akush detail: unknown (service token or endpoint unavailable)")
    return worst, lines


def _bridge_section() -> tuple[str, list[str]]:
    state = _try(lambda: _read_json(MEMORY_DIR / "money_sms_bridge_state.json"))
    if not isinstance(state, dict):
        return "unknown", ["bridge state: unknown"]
    counters = state.get("counters") or state
    dead = counters.get("dead_letter_count", len(state.get("dead_letter", []) or []))
    forwarded = counters.get("forwarded", "?")
    last_id = state.get("last_processed_id") or state.get("last_forwarded_id") or "-"
    worst = "ok" if not dead else "degraded"
    return worst, [f"forwarded {forwarded}, dead-letter {dead}, last {str(last_id)[:16]}"]


def _infra_section() -> tuple[str, list[str]]:
    """Read the latest health-observatory snapshot; report its TRUE state.

    If the observatory's Proxmox scan is failing (e.g. auth_failed), that is
    surfaced degraded -- never silently shown as healthy (no-silent-failure).
    """
    def _latest_snapshot():
        import sqlite3
        db = MEMORY_DIR / "health_observatory.db"
        if not db.exists():
            return None
        con = sqlite3.connect(str(db))
        try:
            row = con.execute(
                "SELECT timestamp, data FROM health_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        ts, data = row
        return ts, json.loads(data)

    snap = _try(_latest_snapshot)
    if not snap:
        return "unknown", ["infra: no observatory snapshot"]
    ts, data = snap
    age_s = None
    if ts:
        try:
            from datetime import datetime

            t = float(ts)
        except (TypeError, ValueError):
            try:
                from datetime import datetime

                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except ValueError:
                t = None
        if t is not None:
            age_s = max(0, int(time.time() - t))
    px = (data or {}).get("proxmox") or {}
    parts = []
    worst = "ok"
    counts_ok = True
    for kind in ("lxc", "qemu"):
        block = px.get(kind) or {}
        err = block.get("error")
        dat = block.get("data")
        if err:
            parts.append(f"{kind} scan {err}")
            counts_ok = False
            worst = "degraded"
        elif isinstance(dat, list):
            on = sum(1 for i in dat if i.get("status") == "running")
            parts.append(f"{kind} {on}/{len(dat)} up")
    if age_s is not None:
        stale = age_s > 7200
        parts.append(f"snapshot age {age_s}s" + (" STALE" if stale else ""))
        if stale:
            worst = worst if worst == "degraded" else "degraded"
    if not parts:
        return "unknown", ["infra: snapshot has no proxmox data"]
    return worst, [" · ".join(parts)]




def _brain_section() -> tuple[str, list[str]]:
    tags = _try(lambda: _fetch_json(BRAIN_URL))
    if tags and tags.get("models"):
        names = [m.get("name", "") for m in tags["models"]]
        ok = any("qwen3-coder" in n for n in names)
        return ("ok" if ok else "degraded"), [f"ollama ok, qwen3-coder {'present' if ok else 'missing'}"]
    return "bad", ["brain tunnel DOWN (11434 unreachable)"]


def _incident_section(since: float | None, journal_reader: Callable[[], list] | None = None) -> tuple[str, list[str]]:
    entries = []
    if journal_reader is not None:
        entries = _try(journal_reader) or []
    else:
        journal = MEMORY_DIR / "kai_event_journal.jsonl"
        def _tail() -> list:
            if not journal.exists():
                return []
            with journal.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            return lines[-500:]
        entries = _try(_tail) or []
    watch_topics = {
        "money.sms.bridge_failed", "security.alert", "money.anomaly.detected",
        "money.reconciliation.failed", "vpn.failover", "backup.failed",
    }
    counted: dict[str, int] = {}
    for entry in entries:
        try:
            event = json.loads(entry) if isinstance(entry, str) else entry
            topic = event.get("topic") or event.get("event") or ""
            ts = float(event.get("ts") or event.get("timestamp") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if topic in watch_topics and (since is None or ts >= since):
            counted[topic] = counted.get(topic, 0) + 1
    if not counted:
        return "ok", ["no incidents since last heartbeat"]
    worst = "degraded" if sum(counted.values()) < 10 else "bad"
    return worst, [f"{', '.join(f'{t.split(chr(46))[-1]} x{n}' for t, n in sorted(counted.items()))}"]


def _try(fn: Callable):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — probes must never raise
        logger.debug("heartbeat probe failed: %s", type(exc).__name__)
        return None


def _merge_worst(*statuses: str) -> str:
    order = {"bad": 0, "degraded": 1, "unknown": 2, "ok": 3}
    return sorted(statuses, key=lambda s: order.get(s, 2))[0]


def default_bot_client():
    """Best-effort akush client (bot service token). None on any failure."""
    try:
        from core.money_telegram.client import get_client

        return get_client()
    except Exception:  # noqa: BLE001
        return None


def collect_sections(since: float | None = None, bot_client=None) -> list[tuple[str, str, list[str]]]:
    """Return [(title, status, detail_lines)]. All probes exception-isolated."""
    s_status, s_parts = _service_section()
    m_status, m_lines = _money_section(bot_client)
    b_status, b_lines = _bridge_section()
    i_status, i_lines = _infra_section()
    br_status, br_lines = _brain_section()
    inc_status, inc_lines = _incident_section(since)
    return [
        ("Services", s_status, [" · ".join(s_parts)] if s_parts else ["unknown"]),
        ("Money", m_status, m_lines or ["unknown"]),
        ("SMS bridge", b_status, b_lines or ["unknown"]),
        ("Infrastructure", i_status, i_lines or ["unknown"]),
        ("Brain", br_status, br_lines or ["unknown"]),
        ("Incidents", inc_status, inc_lines or ["unknown"]),
    ]


def format_sections(since: float | None = None, bot_client=None, max_lines: int = 30) -> str:
    """Format the system sections for the heartbeat digest (bounded length)."""
    emoji = {"ok": OK_EMOJI, "degraded": WARN_EMOJI, "bad": BAD_EMOJI, "unknown": UNKNOWN_EMOJI}
    lines: list[str] = []
    for title, status, details in collect_sections(since=since, bot_client=bot_client):
        detail = details[0] if details else "unknown"
        extra = f" (+{len(details) - 1} more)" if len(details) > 1 else ""
        lines.append(f"{emoji.get(status, UNKNOWN_EMOJI)} {title}: {detail}{extra}"[:220])
        if len(lines) >= max_lines:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)
