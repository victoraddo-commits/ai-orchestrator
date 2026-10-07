"""Auto-approval loop (roadmap 17Y) — scoped, audited, reversible.

Approves Kai's architecture/deploy gates according to
``core.approval_policy`` (manual | scoped | auto). High-risk builds are never
auto-approved. Runs as its own systemd unit so it needs no build_manager
control-flow changes.

    systemctl status kai-auto-approval
    KAI_APPROVAL_POLICY=manual   # full stop, gates come back
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from core import approval as _approval
from core import approval_policy as policy
from core.build_manager import list_builds, approve_architecture

logger = logging.getLogger(__name__)

APPROVABLE = {"approve_architecture", "approve_deploy"}
OPERATOR = "kai.auto-approver"

# request_id -> first time we saw it (fallback veto-window anchor).
_SEEN: dict[str, float] = {}


def _parse_ts(value) -> float | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _gate_opened_at(req: dict, build: dict) -> float | None:
    """When did this gate open? Prefer durable timestamps so the veto window
    survives an auto-approval restart (in-memory _SEEN does not)."""
    for source, keys in ((build, ("updated", "created")),
                         (req, ("created_at", "requested_at", "updated"))):
        for key in keys:
            ts = _parse_ts(source.get(key))
            if ts is not None:
                return ts
    return None


def _build_for_service(service: str):
    if not service or not service.startswith("kai-build:"):
        return None
    bid = service.split(":", 1)[1]
    for b in list_builds():
        if b.get("id") == bid:
            return b
    return None


def run_once(now: float = None) -> list:
    """Approve eligible gates. Returns the list of approved request ids."""
    now = now if now is not None else time.time()
    approved = []

    requests = _approval.load_requests()
    services = {r.get("service") for r in requests}

    for req in requests:
        if req.get("status") != "pending" or req.get("action") not in APPROVABLE:
            continue
        build = _build_for_service(req.get("service")) or {}
        opened = _gate_opened_at(req, build)
        if opened is None:
            opened = _SEEN.setdefault(req["id"], now)
        ok, reason = policy.should_auto_approve(build, now=now, requested_at=opened)
        if not ok:
            logger.info("hold %s %s: %s", req.get("action"), req.get("service"), reason)
            continue
        _approval.approve(req["id"], operator=OPERATOR)
        approved.append(req["id"])
        logger.info("auto-approved %s %s (%s)",
                    req.get("action"), req.get("service"), reason)

    # Orphaned waits (no approval request was ever created).
    for build in list_builds():
        if (build.get("status") == "WAITING_FOR_ARCHITECTURE_APPROVAL"
                and f"kai-build:{build['id']}" not in services):
            ok, reason = policy.should_auto_approve(build, now=now)
            if ok:
                approve_architecture(build["id"], operator=OPERATOR,
                                     note=f"auto: {reason}")
                approved.append(build["id"])
    return approved


def main(interval: int = 30) -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("kai auto-approval started (policy=%s)", policy.policy())
    while True:
        try:
            run_once()
        except Exception as exc:  # never let one bad cycle kill the loop
            logger.error("auto-approval cycle error: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    main()
