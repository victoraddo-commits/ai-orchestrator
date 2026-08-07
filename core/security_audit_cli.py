"""Phase 18A-b: Security Audit CLI.

CLI handler for running security audits and hardening operations.
Usage:
    python -m core.security_audit_cli audit [--scope files,network] [--json]
    python -m core.security_audit_cli harden [--scope files] [--dry-run] [--yes]
    python -m core.security_audit_cli scan [--scope files,network] [--json]
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cmd_audit(args: list) -> None:
    scope = None
    output_format = "text"

    i = 0
    while i < len(args):
        if args[i] == "--scope" and i + 1 < len(args):
            scope = args[i + 1]
            i += 2
        elif args[i] == "--json":
            output_format = "json"
            i += 1
        else:
            i += 1

    base_dir = os.getcwd()

    from core.security_audit.audit import run_audit
    result = run_audit(base_dir=base_dir, scope=scope)

    if output_format == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    _print_audit_report(result)


def cmd_harden(args: list) -> None:
    scope = None
    dry_run = False
    auto_confirm = False

    i = 0
    while i < len(args):
        if args[i] == "--scope" and i + 1 < len(args):
            scope = args[i + 1]
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        elif args[i] == "--yes":
            auto_confirm = True
            i += 1
        else:
            i += 1

    base_dir = os.getcwd()

    if not auto_confirm and not dry_run:
        print("WARNING: Hardening will modify file permissions and configurations.")
        response = input("Continue? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted.")
            return

    from core.security_audit.hardening import run_hardening
    result = run_hardening(
        base_dir=base_dir,
        scope=scope,
        dry_run=dry_run,
        auto_confirm=auto_confirm,
    )

    _print_hardening_report(result)


def cmd_scan(args: list) -> None:
    """Run audit and print a consolidated scan report."""
    cmd_audit(args)


def _print_audit_report(report: dict) -> None:
    summary = report.get("summary", {})
    total = summary.get("total_findings", 0)
    by_sev = summary.get("by_severity", {})
    highest = summary.get("highest_severity", "none")

    print(f"\n{'='*60}")
    print(f"  SECURITY AUDIT REPORT")
    print(f"{'='*60}")
    print(f"  Audit ID:      {report.get('audit_id', 'N/A')}")
    print(f"  Timestamp:     {report.get('timestamp', 'N/A')}")
    print(f"  Base dir:      {report.get('base_dir', 'N/A')}")
    print(f"  Scope:         {report.get('scope', 'N/A')}")
    print(f"  Total findings: {total}")
    print(f"  Highest severity: {highest}")
    print(f"  By severity:   C:{by_sev.get('critical',0)} H:{by_sev.get('high',0)} "
          f"M:{by_sev.get('medium',0)} L:{by_sev.get('low',0)} I:{by_sev.get('info',0)}")
    print(f"{'='*60}\n")

    for section, data in report.items():
        if section in ("audit_id", "timestamp", "base_dir", "scope", "summary"):
            continue
        if not isinstance(data, dict) or "findings" not in data:
            continue

        findings = data.get("findings", [])
        if not findings:
            continue

        print(f"  --- {section} ({len(findings)} findings) ---")
        for f in findings[:10]:
            sev = f.get("severity", "info").upper()
            issue = f.get("issue", "")
            path = f.get("path", f.get("file", f.get("key", "")))
            print(f"    [{sev}] {issue}")
            if path:
                print(f"           {path}")
        if len(findings) > 10:
            print(f"    ... and {len(findings) - 10} more")
        print()


def _print_hardening_report(report: dict) -> None:
    summary = report.get("summary", {})
    applied = summary.get("applied", 0)
    skipped = summary.get("skipped", 0)
    errors = summary.get("errors", 0)
    dry_run = summary.get("dry_run", False)

    mode = "DRY RUN (preview)" if dry_run else "APPLIED"

    print(f"\n{'='*60}")
    print(f"  SECURITY HARDENING REPORT ({mode})")
    print(f"{'='*60}")
    print(f"  Hardening ID:  {report.get('hardening_id', 'N/A')}")
    print(f"  Timestamp:     {report.get('timestamp', 'N/A')}")
    print(f"  Base dir:      {report.get('base_dir', 'N/A')}")
    print(f"  Scope:         {report.get('scope', 'N/A')}")
    print(f"  Applied:       {applied}")
    print(f"  Skipped:       {skipped}")
    print(f"  Errors:        {errors}")
    print(f"{'='*60}\n")

    for section, data in report.items():
        if section in ("hardening_id", "timestamp", "base_dir", "dry_run", "scope", "summary"):
            continue
        if not isinstance(data, dict):
            continue

        applied_list = data.get("applied", [])
        skipped_list = data.get("skipped", [])
        error_list = data.get("errors", [])

        if applied_list:
            print(f"  --- {section}: APPLIED ({len(applied_list)}) ---")
            for a in applied_list:
                path = a.get("path", a.get("file", ""))
                action = a.get("fix_action", a.get("action", ""))
                if dry_run:
                    print(f"    [WOULD-APPLY] {path}: {action}")
                else:
                    print(f"    [APPLIED] {path}: {action}")
            print()

        if skipped_list:
            print(f"  --- {section}: SKIPPED ({len(skipped_list)}) ---")
            for s in skipped_list[:5]:
                path = s.get("path", s.get("file", ""))
                reason = s.get("reason", "unknown")
                print(f"    [SKIPPED] {path}: {reason}")
            if len(skipped_list) > 5:
                print(f"    ... and {len(skipped_list) - 5} more")
            print()

        if error_list:
            print(f"  --- {section}: ERRORS ({len(error_list)}) ---")
            for e in error_list[:5]:
                path = e.get("path", e.get("file", ""))
                reason = e.get("reason", "unknown")
                print(f"    [ERROR] {path}: {reason}")
            if len(error_list) > 5:
                print(f"    ... and {len(error_list) - 5} more")
            print()


USAGE = """Usage:
    python -m core.security_audit_cli audit [--scope <scopes>] [--json]
    python -m core.security_audit_cli harden [--scope <scopes>] [--dry-run] [--yes]
    python -m core.security_audit_cli scan [--scope <scopes>] [--json]

Scopes: files, network, environment, processes, services, dependencies
"""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    command = sys.argv[1]
    remaining = sys.argv[2:]

    if command == "audit":
        cmd_audit(remaining)
    elif command == "harden":
        cmd_harden(remaining)
    elif command == "scan":
        cmd_scan(remaining)
    elif command in ("--help", "-h", "help"):
        print(USAGE)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)
