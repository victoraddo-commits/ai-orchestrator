#!/usr/bin/env python3
"""Legal Brain 2.0 Phase 6 — live validation (Task 5).

Runs each new practice/research tool end-to-end on the live stack (bounded),
plus ``/legal/health`` and a read-only watcher-alert sample, and prints a JSON
summary used to build ``reports/legal_phase6_validation_<date>.md``.

    .venv/bin/python scripts/legal_phase6_validate.py > /tmp/phase6_validate.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import legal_brain_client as lb  # noqa: E402
from core.agentguard.legal_policy import LegalGuard  # noqa: E402
from core.juris_kai import tools  # noqa: E402

CONTRACT = (
    "1. The Supplier shall deliver the goods by 12 March 2021.\n\n"
    "2. The Customer must pay the invoice within 30 days.\n\n"
    "3. If the Customer fails to pay, a penalty of 5% shall apply and the "
    "Customer shall be liable for damages.\n\n"
    "4. Either party may terminate this agreement on 30 days written notice.\n\n"
    "5. The Supplier shall indemnify the Customer against all liabilities.\n"
)
FACTS = (
    "On 12 March 2020 the parties signed the lease. The tenant defaulted on "
    "2021-04-05. A notice to quit was served on 5 April 2021."
)


class RecordingVerifier:
    """A citation verifier that records that the firewall ran."""

    def __init__(self):
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        return {"citations": []}


def _guard(rec):
    return LegalGuard(audit_fn=lambda **k: None, verifier=rec)


def _clip(text, limit=500):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _run(name, fn, *, authority_only, source_kind):
    t0 = time.time()
    rec = RecordingVerifier()
    try:
        out = fn(rec)
        rendered = out.get("rendered", "")
        return {
            "tool": name,
            "ok": True,
            "secs": round(time.time() - t0, 2),
            "authority_only": authority_only,
            "authority_source": source_kind,
            "firewall_applied": rec.calls > 0,
            "authorities": len(out.get("authorities") or []),
            "rendered": _clip(rendered),
        }
    except Exception as exc:  # noqa: BLE001 - validation must record failures
        return {"tool": name, "ok": False, "secs": round(time.time() - t0, 2),
                "error": f"{type(exc).__name__}: {exc}"}


def _watcher_sample():
    """Read-only diff of the live corpus vs the persisted watcher snapshot."""
    try:
        import sqlite3
        from core.legal import watch as w
        from scripts import harvest_cycle as hc

        conn = sqlite3.connect(
            f"file:{hc.ROOT}/data/legal_brain.db?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            prev = w.load_snapshot(hc.DEFAULT_WATCH_SNAPSHOT)
            current = w.build_snapshot(hc._load_doc_rows(conn),
                                       hc._load_meta_by_doc(conn),
                                       repo_seen=hc._load_repo_seen())
        finally:
            conn.close()
        alerted = set((prev or {}).get("alerted") or [])
        changes = [c for c in w.detect_changes(prev, current)
                   if c["identity"] not in alerted]
        alert = w.format_alert(changes, emitted_at=w.utc_now())
        return {"changes": len(changes), "counts": w.change_counts(changes),
                "alert": alert or "(no new changes since the last snapshot)"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main():
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": [],
        "watcher": None,
    }

    # Health (token-gated read).
    try:
        report["health"] = lb.legal_health()
    except Exception as exc:  # noqa: BLE001
        report["health"] = {"error": f"{type(exc).__name__}: {exc}"}

    report["tools"].append(_run(
        "contract_analysis",
        lambda rec: tools.contract_analysis(CONTRACT, title="Sample supply agreement",
                                            guard=_guard(rec)),
        authority_only=False, source_kind="user document (zero-trust workspace)"))
    report["tools"].append(_run(
        "authority_bundle",
        lambda rec: tools.authority_bundle(["breach of contract", "theft"],
                                           guard=_guard(rec)),
        authority_only=True, source_kind="retrieval only"))
    report["tools"].append(_run(
        "legal_chronology",
        lambda rec: tools.legal_chronology(FACTS, guard=_guard(rec)),
        authority_only=False, source_kind="user-provided facts"))
    report["tools"].append(_run(
        "research:statute",
        lambda rec: tools.research("contract", mode="statute",
                                   guard=_guard(rec)),
        authority_only=True, source_kind="retrieval only (enactments)"))
    report["tools"].append(_run(
        "research:case_law",
        lambda rec: tools.research("negligence", mode="case_law",
                                   guard=_guard(rec)),
        authority_only=True, source_kind="retrieval only (judgments)"))
    report["tools"].append(_run(
        "issue_matrix",
        lambda rec: tools.issue_matrix("contract formation",
                                       facts="A agreed to sell goods.",
                                       guard=_guard(rec)),
        authority_only=True, source_kind="retrieval + Deep reasoning"))

    report["watcher"] = _watcher_sample()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
