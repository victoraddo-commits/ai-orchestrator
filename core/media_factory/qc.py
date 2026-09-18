"""Quality control scaffolding (§38).

Runs *real* technical checks when inputs exist (file presence, hashes,
duration via ffprobe) and returns UNVERIFIED / BLOCKED otherwise. Creative and
policy checks are structured but do not invent a verdict where no evidence or
tooling exists.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.media_factory import config, db
from core.media_factory.models import CheckResult
from core.media_factory import assets as assets_mod

logger = logging.getLogger(__name__)


def verify_hash(path: str | Path, expected_sha256: Optional[str]) -> CheckResult:
    actual = assets_mod.sha256_file(path)
    if actual is None:
        return CheckResult("hash", config.STATUS_FAILED, "file missing")
    if not expected_sha256:
        return CheckResult("hash", config.STATUS_UNVERIFIED, "no expected hash on record",
                           evidence={"actual": actual})
    if actual == expected_sha256:
        return CheckResult("hash", config.STATUS_VERIFIED, "sha256 matches",
                           evidence={"sha256": actual})
    return CheckResult("hash", config.STATUS_FAILED, "sha256 mismatch",
                       evidence={"expected": expected_sha256, "actual": actual})


def probe_duration(path: str | Path) -> CheckResult:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return CheckResult("duration", config.STATUS_BLOCKED,
                           config.BLOCKED_EDITING + " (ffprobe unavailable)")
    if not Path(path).is_file():
        return CheckResult("duration", config.STATUS_FAILED, "file missing")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        seconds = float(out.stdout.strip())
        return CheckResult("duration", config.STATUS_VERIFIED, "ffprobe ok",
                           evidence={"seconds": seconds})
    except Exception as exc:  # noqa: BLE001
        return CheckResult("duration", config.STATUS_FAILED,
                           f"ffprobe failed: {type(exc).__name__}")


def run_asset_qc(asset_id: int) -> dict:
    asset = db.query_one("SELECT * FROM assets WHERE id = %s", (asset_id,))
    if not asset:
        return {"status": config.STATUS_MISSING, "asset_id": asset_id,
                "checks": [], "detail": "asset not found"}
    checks: list[CheckResult] = []
    uri = asset.get("uri")
    if uri and "://" not in uri:
        path = Path(uri)
        checks.append(
            CheckResult("exists", config.STATUS_VERIFIED if path.is_file()
                        else config.STATUS_FAILED,
                        "file present" if path.is_file() else "file missing")
        )
        checks.append(verify_hash(path, asset.get("sha256")))
        checks.append(probe_duration(path))
    else:
        checks.append(CheckResult("exists", config.STATUS_UNVERIFIED,
                                  "no local path to verify"))

    creative = CheckResult("creative", config.STATUS_UNVERIFIED,
                           "creative rubric not yet automated (no fabricated verdict)")
    policy = CheckResult("policy", config.STATUS_UNVERIFIED,
                         "run rights.check_publishable for the policy gate")

    statuses = [c.status for c in checks]
    if config.STATUS_FAILED in statuses:
        overall = config.STATUS_FAILED
    elif config.STATUS_BLOCKED in statuses:
        overall = config.STATUS_BLOCKED
    elif checks and all(c.status == config.STATUS_VERIFIED for c in checks):
        overall = config.STATUS_VERIFIED
    else:
        overall = config.STATUS_PARTIALLY_VERIFIED

    result = {
        "status": overall,
        "asset_id": asset_id,
        "checks": [c.to_dict() for c in checks] + [creative.to_dict(), policy.to_dict()],
    }
    db.record_event("quality_control", overall, detail=result)
    db.audit("qc.asset", entity_type="asset", entity_id=asset_id, payload=result)
    return result
