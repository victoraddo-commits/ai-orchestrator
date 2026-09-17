"""KAI Bet — real-money execution gate (§33).

Real-money betting is SEPARATELY GATED and OFF by default. This module never
enables execution; it evaluates the directive's preconditions and reports
whether they are met. Activation requires an explicit operator action.

Env: KAI_BET_REAL_MONEY=1 is the master switch (default 0 = disabled).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List


def master_switch() -> bool:
    return os.environ.get("KAI_BET_REAL_MONEY", "0") == "1"


@dataclass
class RealMoneyStatus:
    enabled: bool
    ready: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""


REQUIRED_CHECKS = (
    "model_calibrated",        # §19 calibration job green
    "paper_performance",       # §17 paper ROI positive over a meaningful sample
    "risk_limits_set",         # §26 limits configured
    "approval_gate",           # §33 human approval on every real bet
    "agentguard",              # §37 permissions enforced
    "vault",                   # §38 secrets only via KAI Vault
    "emergency_stop",          # §34 works and is reachable
    "bankroll_limits",         # §16 staking bounded
    "sportybet_verified",      # §21 exact market verified
)


def evaluate(checks: Dict[str, bool] | None = None) -> RealMoneyStatus:
    """Evaluate real-money readiness from boolean preconditions.

    `enabled` is true only when the master switch is on AND every required
    check passes. Default (no checks supplied) => not ready, disabled.
    """
    checks = checks or {}
    rows = []
    for name in REQUIRED_CHECKS:
        ok = bool(checks.get(name, False))
        rows.append({"check": name, "ok": ok})
    all_ok = all(r["ok"] for r in rows)
    enabled = master_switch() and all_ok
    note = ("Real-money execution is DISABLED. Enable requires KAI_BET_REAL_MONEY=1 "
            "AND all checks green AND an explicit operator action.")
    return RealMoneyStatus(enabled=enabled, ready=all_ok, checks=rows, note=note)
