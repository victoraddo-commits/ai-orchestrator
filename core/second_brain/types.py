"""Types, enums, and base record schema for the Second Brain."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    """The 16 memory types from the KAI spec, grouped by store."""
    # operational store
    INFRASTRUCTURE = "infrastructure"
    INCIDENT = "incident"
    OPERATIONAL = "operational"
    DECISION = "decision"
    # cognitive store
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    TEMPORAL = "temporal"
    # conversational store
    CONVERSATION = "conversation"
    WORKING_MEMORY = "working_memory"
    SHORT_TERM = "short_term"
    # project store
    PROJECT = "project"
    BUSINESS = "business"
    PERSONAL_CONTEXT = "personal_context"
    # relationship store
    RELATIONSHIP = "relationship"
    DOCUMENT = "document"
    # also used directly
    PROCEDURAL = "procedural"


class ChangeType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DEGRADED = "degraded"
    RECOVERED = "recovered"
    DELETED = "deleted"
    MIGRATED = "migrated"
    ROTATED = "rotated"
    ESCALATED = "escalated"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"       # Direct observation / authoritative source
    DOCUMENTED = "documented"     # From docs / specs
    CONVERSATIONAL = "conversational"  # From chat history
    INFERRED = "inferred"         # AI-generated; never silently elevated


class SourceAuthority(int, Enum):
    """7-tier source authority hierarchy."""
    LIVE_SYSTEM = 1        # Live system-of-record
    AUTHORITATIVE_DB = 2   # Authoritative database
    VERSIONED_CONFIG = 3    # Versioned config files (git)
    DOCUMENTATION = 4      # Docs, READMEs, specs
    SECOND_BRAIN = 5       # Second Brain stores
    HISTORICAL_CHAT = 6    # Conversation logs
    INFERENCE = 7          # AI-generated


class MergePolicy(str, Enum):
    NEWEST_WINS = "newest_wins"           # Deduplicate by entity, keep latest timestamp
    SOURCE_AUTHORITY = "source_authority"   # Prefer higher-authority source
    UNION_ALL = "union_all"                # Concatenate all results


# Mapping from MemoryType to its store name and merge policy
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
    """Base record stored in every Second Brain store."""
    entity: str | None = None
    entity_type: str | None = None
    memory_type: MemoryType = MemoryType.OPERATIONAL
    fact: dict[str, Any] = field(default_factory=dict)
    change_type: ChangeType = ChangeType.CREATED
    changed_reason: str = ""
    confidence: Confidence = Confidence.CONFIRMED
    source_authority: SourceAuthority = SourceAuthority.SECOND_BRAIN
    superseded_by: str | None = None   # ID of the record that replaced this one
    ttl_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Internal fields (not set by caller)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    supersedes: str | None = None      # ID of the record this one replaces
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        d = asdict(self)
        # Serialize enums to values
        d["memory_type"] = self.memory_type.value
        d["change_type"] = self.change_type.value
        d["confidence"] = self.confidence.value
        d["source_authority"] = self.source_authority.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SecondBrainRecord:
        d = dict(d)
        d["memory_type"] = MemoryType(d["memory_type"])
        d["change_type"] = ChangeType(d["change_type"])
        d["confidence"] = Confidence(d["confidence"])
        d["source_authority"] = SourceAuthority(d["source_authority"])
        return cls(**d)


@dataclass
class QueryRequest:
    entity: str | None = None
    memory_types: list[MemoryType] | None = None  # None = all types
    time_range: dict[str, str] | None = None      # {"start": ISO, "end": ISO}
    require_confirmation: bool = False             # If True, flag inferred records
    limit: int = 100


@dataclass
class QueryResult:
    records: list[SecondBrainRecord]
    store: str
    merge_policy: MergePolicy
