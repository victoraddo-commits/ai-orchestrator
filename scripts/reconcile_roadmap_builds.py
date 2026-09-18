#!/usr/bin/env python3
"""Reconcile roadmap.json against memory/builds.json (Phase 0.6).

  cd /opt/ai-orchestrator && .venv/bin/python scripts/reconcile_roadmap_builds.py          # dry run
  cd /opt/ai-orchestrator && .venv/bin/python scripts/reconcile_roadmap_builds.py --apply  # apply certain changes

Only phases whose explicit build_id points at a FAILED/ROLLED_BACK build are
auto-applied (status -> failed, roadmap.json backed up first). Phases linked to
failed builds by name only are reported for human review and never applied.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running as `python scripts/reconcile_roadmap_builds.py` puts scripts/ on
# sys.path, not the repo root -- add the root so `import core.*` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import roadmap_reconciliation as rr


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="apply the certain changes (backs up roadmap.json first)")
    parser.add_argument("--roadmap", default=str(rr.ROADMAP_PATH))
    parser.add_argument("--builds", default=str(rr.BUILDS_PATH))
    parser.add_argument("--no-report", action="store_true",
                        help="do not write a markdown report under reports/")
    args = parser.parse_args(argv)

    result = rr.reconcile(
        roadmap_path=args.roadmap,
        builds_path=args.builds,
        apply=args.apply,
    )

    print(rr.render_report(result))

    if not args.no_report:
        report_path = rr.write_report(result)
        print(f"report written: {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
