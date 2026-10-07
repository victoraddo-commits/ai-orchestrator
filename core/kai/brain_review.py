"""Kai Brain review checkpoints (roadmap 22B).

Connects mission-engine review checkpoints to the Kai Brain (kai_brain) via
the existing fabric (``ai_router.delegate``). The Brain reviews an artifact
and returns an approve/reject verdict; the verdict is recorded on the mission
checkpoint so review is part of the mission's timeline, not a side channel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReviewResult:
    approved: bool
    summary: str = ""
    provider: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return {"approved": self.approved, "summary": self.summary,
                "provider": self.provider}


class BrainReviewer:
    """Reviews artifacts with the Kai Brain."""

    def __init__(self, reviewer=None, provider: str = "kai_brain",
                 timeout: int = 240):
        self._reviewer = reviewer
        self.provider = provider
        self.timeout = timeout

    def _call(self, prompt: str) -> str:
        if self._reviewer is not None:
            return self._reviewer(prompt, self.provider, self.timeout)
        from core.ai import ai_router
        result = ai_router.delegate(prompt, task_type="review",
                                    provider=self.provider,
                                    timeout=self.timeout)
        return result.get("response", "") or ""

    def review(self, artifact, kind: str = "change", criteria: str = "") -> ReviewResult:
        prompt = (
            f"Review this {kind} and reply on the first line with APPROVED or "
            f"REJECTED, then a one-line reason.\n"
            + (f"Criteria: {criteria}\n" if criteria else "")
            + f"\nArtifact:\n{artifact}"
        )
        raw = self._call(prompt) or ""
        head = raw.strip().upper()[:40]
        approved = "APPROVED" in head
        summary = raw.strip().splitlines()[0] if raw.strip() else ""
        return ReviewResult(approved=approved, summary=summary,
                            provider=self.provider, raw=raw)


def review_checkpoint(mission_id: str, artifact, kind: str = "change",
                      reviewer: BrainReviewer = None) -> ReviewResult:
    """Review ``artifact`` and record the verdict on the mission checkpoint."""
    result = (reviewer or BrainReviewer()).review(artifact, kind)
    try:
        from core.kai import mission_engine
        mission_engine.checkpoint_mission(
            mission_id, f"brain_review:{kind}",
            artifacts=[{"kind": f"brain_review:{kind}", **result.to_dict()}])
    except Exception:
        pass
    return result
