"""Kai V3 Approval System — Multi-agent approval with configurable voting.

No single AI may approve deployment. Required flow:
  Builder → Architecture → Security → Performance → QA → Docs → Approval → Deploy

Configurable voting threshold. Default: 4/5 approvals required.
"""

from datetime import datetime, timezone

from core.memory import load, save
from core.logger import info

APPROVAL_FILE = "approval_votes.json"

DEFAULT_REVIEWERS = [
    "architecture",
    "security",
    "performance",
    "qa",
    "documentation",
]

# Number of approvals required out of total reviewers
DEFAULT_APPROVAL_THRESHOLD = 4  # 4/5

# Track the current threshold
_approval_threshold = DEFAULT_APPROVAL_THRESHOLD


def set_approval_threshold(threshold: int):
    """Set the approval threshold. Must be between 1 and total reviewers."""
    global _approval_threshold
    if threshold < 1 or threshold > len(DEFAULT_REVIEWERS):
        raise ValueError(
            f"Approval threshold must be between 1 and {len(DEFAULT_REVIEWERS)}"
        )
    _approval_threshold = threshold
    info(f"Approval threshold set to {threshold}/{len(DEFAULT_REVIEWERS)}")


def get_approval_threshold() -> int:
    """Get current approval threshold."""
    return _approval_threshold


def init_build_approval(build_id: str, required_reviewers: list[str] | None = None) -> dict:
    """Initialize an approval record for a build.

    Creates a fresh voting slate with all reviewers in PENDING state.
    """
    reviewers = required_reviewers or DEFAULT_REVIEWERS

    approval = {
        "build_id": build_id,
        "required_reviewers": list(reviewers),
        "threshold": _approval_threshold,
        "votes": {},
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    for reviewer in reviewers:
        approval["votes"][reviewer] = {
            "approved": None,  # None = not yet voted
            "findings": [],
            "confidence": 0.0,
            "voted_at": None,
        }

    _save_approval(build_id, approval)
    return approval


def cast_vote(build_id: str, reviewer: str, approved: bool,
              findings: list[str] | None = None,
              confidence: float = 0.5) -> dict:
    """Cast a review vote for a build.

    Args:
        build_id: The build being reviewed
        reviewer: The reviewer type (architecture, security, etc.)
        approved: Whether this reviewer approves
        findings: List of finding descriptions (for failures)
        confidence: 0.0-1.0 confidence score
    """
    approval = _get_approval(build_id)
    if not approval:
        approval = init_build_approval(build_id)

    if reviewer not in approval["required_reviewers"]:
        raise ValueError(
            f"Reviewer {reviewer} not in required list: "
            f"{approval['required_reviewers']}"
        )

    approval["votes"][reviewer] = {
        "approved": approved,
        "findings": findings or [],
        "confidence": max(0.0, min(1.0, confidence)),
        "voted_at": datetime.now(timezone.utc).isoformat(),
    }
    approval["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Recalculate overall status
    approval["status"] = _calculate_status(approval)

    _save_approval(build_id, approval)
    info(f"Vote cast for {build_id[:12]}: {reviewer}={'APPROVE' if approved else 'REJECT'} "
         f"(status={approval['status']})")

    return approval


def get_approval_status(build_id: str) -> dict | None:
    """Get the current approval status for a build."""
    return _get_approval(build_id)


def get_vote_tally(build_id: str) -> dict:
    """Get a simple tally of votes for a build."""
    approval = _get_approval(build_id)
    if not approval:
        return {"approvals": 0, "rejections": 0, "pending": 0, "total": 0}

    approvals = sum(
        1 for v in approval["votes"].values() if v["approved"] is True
    )
    rejections = sum(
        1 for v in approval["votes"].values() if v["approved"] is False
    )
    pending = sum(
        1 for v in approval["votes"].values() if v["approved"] is None
    )

    return {
        "approvals": approvals,
        "rejections": rejections,
        "pending": pending,
        "total": len(approval["votes"]),
        "threshold": approval.get("threshold", _approval_threshold),
        "status": approval.get("status", "PENDING"),
    }


def is_build_approved(build_id: str) -> bool:
    """Check if a build has received enough approvals for deployment."""
    tally = get_vote_tally(build_id)
    return tally["status"] == "APPROVED"


def is_build_rejected(build_id: str) -> bool:
    """Check if a build has been rejected (too many rejections to ever pass)."""
    tally = get_vote_tally(build_id)
    return tally["status"] == "REJECTED"


def get_pending_voters(build_id: str) -> list[str]:
    """Get list of reviewers who haven't voted yet."""
    approval = _get_approval(build_id)
    if not approval:
        return list(DEFAULT_REVIEWERS)

    return [
        reviewer for reviewer, vote in approval["votes"].items()
        if vote["approved"] is None
    ]


def reset_approval(build_id: str):
    """Reset an approval record — used when a build is resubmitted after fixes."""
    approval = _get_approval(build_id)
    if approval:
        init_build_approval(
            build_id,
            required_reviewers=approval["required_reviewers"],
        )
        info(f"Approval reset for {build_id[:12]}")


def _calculate_status(approval: dict) -> str:
    """Calculate overall approval status from votes."""
    votes = approval["votes"]
    threshold = approval.get("threshold", _approval_threshold)

    approvals = sum(1 for v in votes.values() if v["approved"] is True)
    rejections = sum(1 for v in votes.values() if v["approved"] is False)
    total = len(votes)

    # Enough approvals to pass
    if approvals >= threshold:
        return "APPROVED"

    # Too many rejections to ever reach threshold
    remaining = total - rejections
    if remaining < threshold:
        return "REJECTED"

    # Someone voted but not enough yet
    if approvals > 0 or rejections > 0:
        return "PARTIAL"

    return "PENDING"


def _get_approval(build_id: str) -> dict | None:
    """Get approval record from memory."""
    data = load(APPROVAL_FILE)
    approvals = data.get("records", []) if data else []
    for a in approvals:
        if a.get("build_id") == build_id:
            return a
    return None


def _save_approval(build_id: str, approval: dict):
    """Save approval record to memory."""
    data = load(APPROVAL_FILE)
    if not data:
        data = {"schema_version": 1, "records": []}

    records = data.get("records", [])
    for i, existing in enumerate(records):
        if existing.get("build_id") == build_id:
            records[i] = approval
            data["records"] = records
            save(APPROVAL_FILE, data)
            return

    # New record
    records.append(approval)
    data["records"] = records
    save(APPROVAL_FILE, data)
