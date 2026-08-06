"""Kai V3 Build Contracts — Structured build task definitions.

Every build task requires a contract specifying what can be built,
what files are allowed, expected cost, reviewers needed, and how to roll back.
No build proceeds without a validated contract.
"""

from datetime import datetime

from core.lifecycle import new_object

REQUIRED_CONTRACT_FIELDS = [
    "task_id",
    "objective",
    "acceptance_criteria",
    "files_allowed",
    "files_forbidden",
    "dependencies",
    "required_reviewers",
    "rollback_plan",
]

DEFAULT_EXPECTED_RUNTIME = 600
DEFAULT_EXPECTED_COST = 0.35
DEFAULT_REQUIRED_REVIEWERS = ["architecture", "security", "qa"]


def build_contract_from_phase(phase: dict) -> dict:
    """Convert a roadmap phase into a validated build contract.

    Extracts phase metadata (name, description, priority, dependencies)
    and produces a structured contract dict that the build manager consumes.
    """
    phase_id = phase.get("id", "")
    name = phase.get("name", "unnamed-phase")

    files_allowed = _phase_files_allowed(phase)
    files_forbidden = _phase_files_forbidden(phase)
    reviewers = _phase_reviewers(phase)

    contract = new_object(
        status="CONTRACT_DRAFT",
        trace_id=phase_id,
        phase_id=phase_id,
        phase_name=name,
        objective=phase.get("description", f"Implement {name}"),
        acceptance_criteria=phase.get("acceptance_criteria", []),
        files_allowed=files_allowed,
        files_forbidden=files_forbidden,
        dependencies=phase.get("dependencies", []),
        expected_runtime_s=phase.get("expected_runtime_s", DEFAULT_EXPECTED_RUNTIME),
        expected_cost_usd=phase.get("expected_cost_usd", DEFAULT_EXPECTED_COST),
        required_reviewers=reviewers,
        rollback_plan=phase.get("rollback_plan", "git revert <commit>"),
        priority=phase.get("priority", 50),
    )
    return contract


def validate_contract(contract: dict) -> tuple[bool, list[str]]:
    """Validate that a contract has all required fields and sensible values.

    Returns (is_valid, list_of_errors).
    """
    errors = []

    for field in REQUIRED_CONTRACT_FIELDS:
        if field not in contract:
            errors.append(f"Missing required field: {field}")

    files_allowed = contract.get("files_allowed", [])
    files_forbidden = contract.get("files_forbidden", [])

    if not isinstance(files_allowed, list):
        errors.append("files_allowed must be a list")
    if not isinstance(files_forbidden, list):
        errors.append("files_forbidden must be a list")

    # Check for overlap — a file cannot be both allowed and forbidden
    overlap = set(files_allowed) & set(files_forbidden)
    if overlap:
        errors.append(f"Files in both allowed and forbidden: {overlap}")

    reviewers = contract.get("required_reviewers", [])
    valid_reviewers = {
        "architecture", "security", "performance", "qa", "documentation"
    }
    invalid = set(reviewers) - valid_reviewers
    if invalid:
        errors.append(f"Unknown reviewer types: {invalid}")

    return len(errors) == 0, errors


def normalize_contract(contract: dict) -> dict:
    """Fill in defaults for a contract, ensuring all optional fields are present."""
    defaults = {
        "acceptance_criteria": [],
        "files_allowed": ["*"],
        "files_forbidden": [],
        "dependencies": [],
        "expected_runtime_s": DEFAULT_EXPECTED_RUNTIME,
        "expected_cost_usd": DEFAULT_EXPECTED_COST,
        "required_reviewers": DEFAULT_REQUIRED_REVIEWERS,
        "rollback_plan": "git revert <commit>",
    }
    for key, default in defaults.items():
        if key not in contract:
            contract[key] = default
    return contract


def _phase_files_allowed(phase: dict) -> list[str]:
    """Determine which files a phase is allowed to modify.

    Default: allow all project files. Phases can constrain this.
    """
    return phase.get("files_allowed", ["*"])


def _phase_files_forbidden(phase: dict) -> list[str]:
    """Determine which files a phase must never touch.

    Default: forbid nothing beyond what sandbox isolation prevents.
    """
    forbidden = set(phase.get("files_forbidden", []))

    # Universal forbiddens — no build may touch these
    forbidden.update([
        "core/v3/*.py",
        "core/memory.py",
        "core/lifecycle.py",
        "memory/",
        ".env",
    ])

    return list(forbidden)


def _phase_reviewers(phase: dict) -> list[str]:
    """Determine which reviewers are required for a phase.

    Critical infrastructure phases require all 5 reviewers.
    Standard phases use the default 3.
    """
    priority = phase.get("priority", 50)

    # Priority 1-10: critical infrastructure — full review
    if priority <= 10:
        return ["architecture", "security", "performance", "qa", "documentation"]

    # Priority 11-30: high importance — 4 reviewers
    if priority <= 30:
        return ["architecture", "security", "qa", "documentation"]

    # Default: 3 reviewers
    return DEFAULT_REQUIRED_REVIEWERS
