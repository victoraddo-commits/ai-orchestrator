"""Second Brain type definitions — enums, record schema, query schemas."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    """All 16 memory categories in the unified memory system."""

    INFRASTRUCTURE = "INFRASTRUCTURE"
    INCIDENT = "INCIDENT"
    OPERATIONAL = "OPERATIONAL"
    DECISION = "DECISION"
    SEMANTIC = "SEMANTIC"
    EPISODIC = "EPISODIC"
    TEMPORAL = "TEMPORAL"
    CONVERSATION = "CONVERSATION"
    WORKING_MEMORY = "WORKING_MEMORY"
    SHORT_TERM = "SHORT_TERM"
    PROJECT = "PROJECT"
    BUSINESS = "BUSINESS"
    PERSONAL_CONTEXT = "PERSONAL_CONTEXT"
    RELATIONSHIP = "RELATIONSHIP"
    DOCUMENT = "DOCUMENT"
    PROCEDURAL = "PROCEDURAL"


class ChangeType(str, Enum):
    """Lifecycle event that produced this record."""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    DEGRADED = "DEGRADED"
    RECOVERED = "RECOVERED"
    DELETED = "DELETED"
    MIGRATED = "MIGRATED"
    ROTATED = "ROTATED"
    ESCALATED = "ESCALATED"


class Confidence(str, Enum):
    """How firmly this fact is established."""

    CONFIRMED = "CONFIRMED"
    DOCUMENTED = "DOCUMENTED"
    CONVERSATIONAL = "CONVERSATIONAL"
    INFERRED = "INFERRED"


class SourceAuthority(int, Enum):
    """Provenance tier — lower number = higher authority."""

    LIVE_SYSTEM = 1
    AUTHORITATIVE_DB = 2
    VERSIONED_CONFIG = 3
    DOCUMENTATION = 4
    SECOND_BRAIN = 5
    HISTORICAL_CHAT = 6
    INFERENCE = 7


class MergePolicy(str, Enum):
    """How conflicting facts for the same entity are resolved."""

    NEWEST_WINS = "NEWEST_WINS"
    SOURCE_AUTHORITY = "SOURCE_AUTHORITY"
    UNION_ALL = "UNION_ALL"


#: Maps each MemoryType to its backing store and merge strategy.
MEMORY_TYPE_STORE: dict[MemoryType, tuple[str, MergePolicy]] = {
    MemoryType.INFRASTRUCTURE: ("operational", MergePolicy.NEWEST_WINS),
    MemoryType.INCIDENT: ("operational", MergePolicy.NEWEST_WINS),
    MemoryType.OPERATIONAL: ("operational", MergePolicy.NEWEST_WINS),
    MemoryType.DECISION: ("operational", MergePolicy.NEWEST_WINS),
    MemoryType.SEMANTIC: ("cognitive", MergePolicy.SOURCE_AUTHORITY),
    MemoryType.EPISODIC: ("cognitive", MergePolicy.SOURCE_AUTHORITY),
    MemoryType.TEMPORAL: ("cognitive", MergePolicy.SOURCE_AUTHORITY),
    MemoryType.CONVERSATION: ("conversational", MergePolicy.NEWEST_WINS),
    MemoryType.WORKING_MEMORY: ("conversational", MergePolicy.NEWEST_WINS),
    MemoryType.SHORT_TERM: ("conversational", MergePolicy.NEWEST_WINS),
    MemoryType.PROJECT: ("project", MergePolicy.NEWEST_WINS),
    MemoryType.BUSINESS: ("project", MergePolicy.NEWEST_WINS),
    MemoryType.PERSONAL_CONTEXT: ("project", MergePolicy.NEWEST_WINS),
    MemoryType.RELATIONSHIP: ("relationship", MergePolicy.UNION_ALL),
    MemoryType.DOCUMENT: ("relationship", MergePolicy.UNION_ALL),
    MemoryType.PROCEDURAL: ("project", MergePolicy.NEWEST_WINS),
}


@dataclass
class SecondBrainRecord:
    """Canonical record stored in the Second Brain."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity: str | None = None
    entity_type: str | None = None
    memory_type: MemoryType = MemoryType.OPERATIONAL
    fact: dict[str, Any] = field(default_factory=dict)
    change_type: ChangeType = ChangeType.CREATED
    changed_reason: str = ""
    confidence: Confidence = Confidence.CONFIRMED
    source_authority: SourceAuthority = SourceAuthority.SECOND_BRAIN
    supersedes: str | None = None
    superseded_by: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-serializable dict (enum values become plain strings/int)."""
        d = asdict(self)
        d["memory_type"] = self.memory_type.value
        d["change_type"] = self.change_type.value
        d["confidence"] = self.confidence.value
        d["source_authority"] = self.source_authority.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecondBrainRecord:
        """Deserialize from a dict, converting plain values back to enum members."""
        mt = MemoryType(data["memory_type"]) if isinstance(data["memory_type"], str) else data["memory_type"]
        ct = ChangeType(data["change_type"]) if isinstance(data["change_type"], str) else data["change_type"]
        cf = Confidence(data["confidence"]) if isinstance(data["confidence"], str) else data["confidence"]
        sa = SourceAuthority(data["source_authority"]) if isinstance(data["source_authority"], (str, int)) else data["source_authority"]
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            entity=data.get("entity"),
            entity_type=data.get("entity_type"),
            memory_type=mt,
            fact=data.get("fact", {}),
            change_type=ct,
            changed_reason=data.get("changed_reason", ""),
            confidence=cf,
            source_authority=sa,
            supersedes=data.get("supersedes"),
            superseded_by=data.get("superseded_by"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            ttl_seconds=data.get("ttl_seconds"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class QueryRequest:
    """Filter criteria for a Second Brain lookup."""

    entity: str | None = None
    memory_types: list[MemoryType] | None = None
    time_range: dict[str, str] | None = None  # {"start": ISO, "end": ISO}
    require_confirmation: bool = False
    limit: int = 100


@dataclass
class QueryResult:
    """Result set from a Second Brain query."""

    records: list[SecondBrainRecord]
    store: str = ""
    merge_policy: MergePolicy = MergePolicy.NEWEST_WINS
