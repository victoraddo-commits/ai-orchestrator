"""Scoped auto-approval (roadmap 17Y).

Replaces blanket gate removal with risk-scoped auto-approval plus an operator
veto window:

- ``KAI_APPROVAL_POLICY=manual``  → every gate stays human (old behavior).
- ``KAI_APPROVAL_POLICY=scoped``  → auto-approve LOW-risk builds only, after
  ``KAI_APPROVAL_VETO_SECONDS`` (default 300s) so the operator can veto.
- ``KAI_APPROVAL_POLICY=auto``    → auto-approve everything (full autonomy).

High-risk builds (security-critical paths, infra/network/secret/DB/payment/
legal surfaces) are NEVER auto-approved, even under ``auto`` policy.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

try:
    from core.authz import SECURITY_CRITICAL_PATHS
except Exception:  # pragma: no cover - authz always present in prod
    SECURITY_CRITICAL_PATHS = set()

HIGH_RISK_KEYWORDS = (
    "vault secret", "rotate secret", "secrets rotation", "private key",
    "api key", "credential", "proxmox", "opnsense", "firewall",
    "wireguard", "tailscale", "vpn config", "database migration",
    "schema migration", "payment", "billing", "hubtel", "legal contract",
)

VALID_POLICIES = {"manual", "scoped", "auto"}


def policy() -> str:
    value = os.environ.get("KAI_APPROVAL_POLICY", "scoped").strip().lower()
    return value if value in VALID_POLICIES else "scoped"


def veto_window_seconds() -> int:
    try:
        return max(0, int(os.environ.get("KAI_APPROVAL_VETO_SECONDS", "300")))
    except (ValueError, TypeError):
        return 300


def classify(build: dict) -> tuple:
    """Return (risk, reason). High-risk gates always stay human."""
    files = (build.get("generation_result") or {}).get("files_changed") or []
    for f in files:
        if f in SECURITY_CRITICAL_PATHS:
            return ("high", f"security-critical path: {f}")

    # Keyword scan is restricted to the build NAME + DESCRIPTION (not the
    # verbose plan, which mentions many subsystems and produced false
    # positives like "router" matching "routing").
    text = " ".join(
        str(build.get(k, "")) for k in ("name", "description")).lower()
    for keyword in HIGH_RISK_KEYWORDS:
        if keyword in text:
            return ("high", f"high-risk keyword: {keyword!r}")
    return ("low", "no high-risk signals")


def should_auto_approve(build: dict, now: float = None,
                        requested_at: float = None) -> tuple:
    """Return (approve: bool, reason: str) for the build's open gate."""
    p = policy()
    if p == "manual":
        return (False, "policy=manual")

    generation = build.get("generation_result") or {}
    if generation and not generation.get("success", True):
        return (False, "generation not successful")

    security = build.get("security_report") or {}
    if str(security.get("highest_severity", "")).lower() in ("high", "critical"):
        return (False, "high/critical security findings")

    risk, reason = classify(build)
    if risk != "low":
        return (False, f"high-risk build: {reason}")
    if p == "auto":
        return (True, f"policy=auto ({reason})")

    opened = requested_at if requested_at is not None else build.get("_gate_requested_at")
    if opened:
        age = (now if now is not None else time.time()) - float(opened)
        window = veto_window_seconds()
        if age < window:
            return (False, f"veto window active ({int(age)}s < {window}s)")
    return (True, f"scoped auto-approve ({reason})")
