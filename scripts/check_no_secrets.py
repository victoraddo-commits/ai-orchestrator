#!/usr/bin/env python3
"""Fail if a hardcoded secret appears in a git-tracked file.

Why this exists
---------------
Real Telegram bot tokens were committed into tracked docs/specs/reports and a
code docstring (GitHub secret-scanning flagged them). Tokens live in git
history now, which cannot be un-leaked by editing the working tree; the only
real remediation is rotation (see docs/JURIS_KAI_TOKEN_ROTATION_GUIDE.md).
This guard stops the *next* one from being committed.

Usage
-----
    python scripts/check_no_secrets.py            # scan tracked files, exit 1 on hit
    python scripts/check_no_secrets.py --staged   # only git-staged files (pre-commit)

Exit status is 0 when clean, 1 when any unallowlisted secret is found.

Findings are always masked -- the tool never prints a discovered value.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Telegram bot tokens: "<8-10 digit bot id>:<33+ char secret>". The trailing
# secret class is what the real tokens use (base64url-ish). The negative
# lookbehind keeps us from matching a suffix of a longer id.
TELEGRAM_BOT_TOKEN = re.compile(r"(?<![0-9A-Za-z_])(\d{8,10}):([A-Za-z0-9_-]{33,})")

# Additional high-signal provider key shapes. Kept narrow on purpose: broad
# patterns like a bare ``sk-`` match ordinary words ("risk-management") and
# would train people to ignore the guard.
GENERIC_SECRETS = {
    "stripe_live_key": re.compile(r"sk_live_[A-Za-z0-9]{16,}"),
    "openai_project_key": re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    "github_pat": re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    "tailscale_key": re.compile(r"tskey-[A-Za-z0-9]{16,}"),
}

# The Telegram bridge test suite intentionally uses this dummy token to assert
# redaction. Anchoring on the bot id + secret prefix keeps the allowlist from
# ever hiding a real token that merely shares a few characters.
ALLOWED_TELEGRAM_TOKENS = re.compile(r"^123456789:AABB[A-Za-z0-9_-]*$")

# Directories that are never worth scanning even if somehow tracked.
SKIPPED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}


@dataclass(frozen=True)
class Finding:
    filename: str
    line: int
    rule: str
    masked_secret: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}: [{self.rule}] {self.masked_secret}"


def mask_secret(value: str) -> str:
    """Return a triage-safe rendering of a secret; never the raw value."""
    if ":" in value:
        head, _, tail = value.partition(":")
        return f"{head}:{tail[:4]}{'*' * 6}({len(tail)})"
    return f"{value[:6]}{'*' * 6}({len(value)})"


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def find_secrets(text: str, filename: str = "<string>") -> list[Finding]:
    """Return every unallowlisted secret-shaped match in ``text``."""
    findings: list[Finding] = []

    for match in TELEGRAM_BOT_TOKEN.finditer(text):
        token = match.group(0)
        if ALLOWED_TELEGRAM_TOKENS.match(token):
            continue
        findings.append(
            Finding(filename, _line_number(text, match.start()),
                    "telegram_bot_token", mask_secret(token))
        )

    for rule, pattern in GENERIC_SECRETS.items():
        for match in pattern.finditer(text):
            findings.append(
                Finding(filename, _line_number(text, match.start()),
                        rule, mask_secret(match.group(0)))
            )

    return findings


def scan_file(path: Path, display_name: str | None = None) -> list[Finding]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    return find_secrets(text, display_name or str(path))


def tracked_files(root: Path, staged_only: bool = False) -> list[str]:
    """List git-tracked (or staged) paths, relative to ``root``."""
    args = ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    if not staged_only:
        args = ["git", "-C", str(root), "ls-files"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def scan_repository(root: Path, staged_only: bool = False) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for rel in tracked_files(root, staged_only=staged_only):
        parts = Path(rel).parts
        if any(part in SKIPPED_DIRS for part in parts):
            continue
        findings.extend(scan_file(root / rel, display_name=rel))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument(
        "--staged", action="store_true",
        help="scan only git-staged files (used by the pre-commit hook)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    findings = scan_repository(root, staged_only=args.staged)

    if not findings:
        print("check_no_secrets: OK -- no hardcoded secrets in tracked files")
        return 0

    print("check_no_secrets: FAIL -- hardcoded secret(s) detected:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(
        "\nMove the value to an environment variable, redact the file, and "
        "rotate the credential if it was ever committed.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
