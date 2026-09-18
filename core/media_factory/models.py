"""KAI Media Revenue Factory — enums and lightweight dataclasses.

Pure data definitions; no database or network access. These enums are the
Python mirror of the Postgres enums created in ``migrations/001_init.sql``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.media_factory import config


class MediaStatus(str, Enum):
    VERIFIED = config.STATUS_VERIFIED
    PARTIALLY_VERIFIED = config.STATUS_PARTIALLY_VERIFIED
    UNVERIFIED = config.STATUS_UNVERIFIED
    MISSING = config.STATUS_MISSING
    BLOCKED = config.STATUS_BLOCKED
    DEGRADED = config.STATUS_DEGRADED
    FAILED = config.STATUS_FAILED


class PublishMode(str, Enum):
    DRY_RUN = "dry_run"
    TEST = "test"
    CANARY = "canary"
    LIVE = "live"


class PatternState(str, Enum):
    HYPOTHESIS = "HYPOTHESIS"
    EMERGING = "EMERGING"
    VALIDATED = "VALIDATED"
    STRATEGIC = "STRATEGIC"
    DECLINED = "DECLINED"


class ContentState(str, Enum):
    IDEA = "IDEA"
    RESEARCH = "RESEARCH"
    APPROVED_CONCEPT = "APPROVED_CONCEPT"
    SCRIPTING = "SCRIPTING"
    ASSET_GENERATION = "ASSET_GENERATION"
    ASSEMBLY = "ASSEMBLY"
    RENDERING = "RENDERING"
    QC = "QC"
    RIGHTS_CHECK = "RIGHTS_CHECK"
    POLICY_CHECK = "POLICY_CHECK"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    ANALYTICS_PENDING = "ANALYTICS_PENDING"
    ANALYZING = "ANALYZING"
    WINNER = "WINNER"
    REPAIR = "REPAIR"
    RETEST = "RETEST"
    DECLINING = "DECLINING"
    RETIRED = "RETIRED"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"


@dataclass
class Capability:
    """One probed capability with an honest status."""

    name: str
    status: str
    detail: str
    blocked_reason: Optional[str] = None
    evidence: Optional[dict] = None
    verified: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "blocked_reason": self.blocked_reason,
            "verified": self.verified,
            "evidence": self.evidence or {},
        }


@dataclass
class StageResult:
    """Outcome of one media pipeline stage."""

    stage: str
    status: str
    detail: str = ""
    blocked_reason: Optional[str] = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "blocked_reason": self.blocked_reason,
            "data": self.data,
        }


@dataclass
class TrendSignal:
    """A normalized, source-derived trend signal (no invented values)."""

    source: str
    title: str
    external_id: Optional[str] = None
    geo: Optional[str] = None
    traffic: Optional[int] = None
    published: Optional[str] = None
    news_count: int = 0
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "external_id": self.external_id,
            "geo": self.geo,
            "traffic": self.traffic,
            "published": self.published,
            "news_count": self.news_count,
            "raw": self.raw,
        }


@dataclass
class TrendScore:
    """Rule-based scores derived exclusively from fetched signal fields."""

    momentum: float
    competition: float
    shelf_life: float
    score: float
    formula: str = ""

    def to_dict(self) -> dict:
        return {
            "momentum": round(self.momentum, 4),
            "competition": round(self.competition, 4),
            "shelf_life": round(self.shelf_life, 4),
            "score": round(self.score, 4),
            "formula": self.formula,
        }


@dataclass
class Opportunity:
    """Scored content opportunity derived from a validated trend."""

    factory_key: str
    angle: str
    effort: float
    expected_payoff: float
    score: float
    formula: str = ""

    def to_dict(self) -> dict:
        return {
            "factory_key": self.factory_key,
            "angle": self.angle,
            "effort": round(self.effort, 4),
            "expected_payoff": round(self.expected_payoff, 4),
            "score": round(self.score, 4),
            "formula": self.formula,
        }


@dataclass
class CheckResult:
    """Result of a single QC check."""

    name: str
    status: str
    detail: str = ""
    evidence: Optional[Any] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }
