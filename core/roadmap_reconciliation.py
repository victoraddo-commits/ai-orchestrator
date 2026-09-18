"""Phase 0.6: reconcile roadmap.json against memory/builds.json.

2026-09-18 audit finding: the runner's roadmap.json reported 277/281 phases
completed and 0 pending, while memory/builds.json held 119 FAILED of 120
builds. A phase whose own build is terminal-failed cannot be complete.

This module is conservative by design:

  * ``certain``  -- the phase carries an explicit ``build_id`` that resolves
    to a FAILED/ROLLED_BACK build while the phase still claims to be
    completed. Only these are auto-applied (status -> "failed").
  * ``proposed`` -- the phase has no ``build_id`` but builds named after the
    phase id exist, none COMPLETED and at least one failed. Reported for
    human review; never auto-applied, because name-only linkage is weaker
    evidence than an explicit build_id.

``roadmap.json`` is always backed up before an apply; dry-run changes nothing.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP_PATH = ROOT / "roadmap.json"
BUILDS_PATH = ROOT / "memory" / "builds.json"
REPORTS_DIR = ROOT / "reports"

FAILED_BUILD_STATUSES = {"FAILED", "ROLLED_BACK"}
# A phase already in one of these states is terminal for reconciliation
# purposes -- reopening it would overwrite a human decision.
SKIP_PHASE_STATUSES = {"failed", "cancelled"}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_json(path: Path):
    return json.loads(Path(path).read_text())


def load_roadmap(path: Path = ROADMAP_PATH) -> dict:
    data = _read_json(path)
    if isinstance(data, dict) and "phases" in data:
        return data
    if isinstance(data, list):
        return {"schema_version": 1, "phases": data}
    raise ValueError(f"unrecognised roadmap shape: {type(data)!r}")


def load_builds(path: Path = BUILDS_PATH) -> list:
    data = _read_json(path)
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    if isinstance(data, list):
        return data
    return []


def compute_reconciliation(phases: list, builds: list) -> dict:
    """Return ``{"certain": [...], "proposed": [...]}`` change descriptors."""
    by_id = {b.get("id"): b for b in builds if b.get("id")}
    by_name: dict[str, list] = {}
    for build in builds:
        by_name.setdefault(build.get("name"), []).append(build)

    certain: list[dict] = []
    proposed: list[dict] = []

    for phase in phases:
        status = phase.get("status")
        if status in SKIP_PHASE_STATUSES:
            continue
        if status != "completed":
            # Non-completed phases are already visible to advance_roadmap();
            # only the false "completed" bookkeeping is reconciled here.
            continue

        phase_id = phase.get("id")
        build_id = phase.get("build_id")

        if build_id and build_id in by_id:
            build = by_id[build_id]
            if build.get("status") in FAILED_BUILD_STATUSES:
                certain.append({
                    "phase_id": phase_id,
                    "from_status": status,
                    "action": "reopen_failed",
                    "build_id": build_id,
                    "build_status": build.get("status"),
                    "reason": f"linked build {build_id} is {build.get('status')}",
                })
            continue

        named = by_name.get(phase_id) or []
        if not named:
            continue
        if any(b.get("status") == "COMPLETED" for b in named):
            continue
        failed = [b for b in named if b.get("status") in FAILED_BUILD_STATUSES]
        if failed:
            proposed.append({
                "phase_id": phase_id,
                "from_status": status,
                "action": "review_reopen_failed",
                "build_ids": [b.get("id") for b in failed],
                "reason": (
                    f"{len(failed)} build(s) named {phase_id!r} failed and none "
                    f"completed (no explicit build_id to confirm)"
                ),
            })

    return {"certain": certain, "proposed": proposed}


def apply_changes(roadmap: dict, changes: list[dict]) -> dict:
    """Apply ``certain`` changes in place and annotate the roadmap. Returns the
    same dict for convenience."""
    by_phase = {c["phase_id"]: c for c in changes}
    stamp = datetime.now(timezone.utc).isoformat()

    for phase in roadmap.get("phases", []):
        change = by_phase.get(phase.get("id"))
        if not change:
            continue
        phase["status"] = "failed"
        phase["failure_reason"] = (
            f"Roadmap/build reconciliation: {change['reason']}"
        )
        phase["reconciled_at"] = stamp

    roadmap["last_reconciled"] = stamp
    roadmap["reconciliation_note"] = (
        f"Reopened {len(changes)} phase(s) whose linked build is FAILED; "
        f"see reports/ for the full reconciliation report."
    )
    return roadmap


def backup_roadmap(path: Path = ROADMAP_PATH) -> Path:
    backup = Path(path).with_name(f"{Path(path).name}.bak-{_utc_stamp()}-reconcile-builds")
    shutil.copy2(path, backup)
    return backup


def reconcile(
    roadmap_path: Path = ROADMAP_PATH,
    builds_path: Path = BUILDS_PATH,
    apply: bool = False,
):
    """Compute the reconciliation. With ``apply=True`` back up roadmap.json and
    write the certain changes; always returns the result envelope."""
    roadmap_path = Path(roadmap_path)
    roadmap = load_roadmap(roadmap_path)
    builds = load_builds(builds_path)
    changes = compute_reconciliation(roadmap.get("phases", []), builds)

    backup = None
    if apply and changes["certain"]:
        backup = backup_roadmap(roadmap_path)
        apply_changes(roadmap, changes["certain"])
        roadmap_path.write_text(json.dumps(roadmap, indent=2) + "\n")

    return {
        "roadmap": str(roadmap_path),
        "builds": str(builds_path),
        "applied": bool(apply and changes["certain"]),
        "backup": str(backup) if backup else None,
        "certain": changes["certain"],
        "proposed": changes["proposed"],
    }


def render_report(result: dict) -> str:
    lines = [
        "# Roadmap <-> builds reconciliation report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Roadmap: `{result['roadmap']}`",
        f"Builds:  `{result['builds']}`",
        f"Applied: {result['applied']}",
        f"Backup:  {result['backup'] or '(none — dry run or no certain changes)'}",
        "",
        f"## Certain (auto-applied): {len(result['certain'])}",
        "",
    ]
    if result["certain"]:
        for change in result["certain"]:
            lines.append(
                f"- `{change['phase_id']}`: {change['from_status']} -> failed "
                f"({change['reason']})"
            )
    else:
        lines.append("- none")

    lines += [
        "",
        f"## Proposed only (human review, NOT applied): {len(result['proposed'])}",
        "",
    ]
    if result["proposed"]:
        for change in result["proposed"]:
            lines.append(f"- `{change['phase_id']}`: {change['reason']}")
    else:
        lines.append("- none")

    lines.append("")
    return "\n".join(lines)


def write_report(result: dict, reports_dir: Path = REPORTS_DIR) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"roadmap_build_reconciliation_{_utc_stamp()}.md"
    path.write_text(render_report(result))
    return path
