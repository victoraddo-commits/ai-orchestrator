import re

from core.action_policy import risk_tier


class SecurityViolation(RuntimeError):
    """Raised when an action or command fails a mandatory safety check."""


DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\*",
    r"mkfs\.",
    r"dd\s+if=.*of=/dev/",
    r":\(\)\s*\{.*:\|:.*\}\s*;\s*:",
    r"iptables\s+(-F|--flush)",
    r"ufw\s+disable",
    r"useradd\b",
    r"\bpasswd\b",
    r"chmod\s+777\s+/",
    r"chown\s+.*\s+/etc",
    r"/etc/shadow",
    r"id_rsa",
    r"\.ssh/authorized_keys",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]


def is_dangerous_command(command):
    return any(pattern.search(command) for pattern in _COMPILED_PATTERNS)


def is_known_action(action):
    return risk_tier(action) != "unknown"


def enforce_action_is_safe(action, command):

    if not is_known_action(action):
        raise SecurityViolation(
            f"Action {action!r} is not in the action policy allowlist"
        )

    if is_dangerous_command(command):
        raise SecurityViolation(
            f"Blocked dangerous command pattern in: {command!r}"
        )
