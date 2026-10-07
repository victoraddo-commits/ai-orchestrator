"""KAI Knowledge Fabric — data model (Primary + Secondary knowledge).

Reuses KLAUS (PostgreSQL + pgvector) as the backend; this package defines the
tier/ACL/authorization semantics that must gate every retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class KnowledgeTier(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class Visibility(str, Enum):
    PRIVATE = "PRIVATE"
    GROUP = "GROUP"
    SELECTED_USERS = "SELECTED_USERS"
    KNOWLEDGE_SPACE = "KNOWLEDGE_SPACE"
    ADMIN_ONLY = "ADMIN_ONLY"


class DocRole(str, Enum):
    READER = "READER"
    EDITOR = "EDITOR"
    MANAGER = "MANAGER"
    OWNER = "OWNER"


@dataclass
class Principal:
    """The authenticated principal for a request (from KAI ID)."""
    user_id: str
    group_ids: set = field(default_factory=set)
    space_ids: set = field(default_factory=set)
    roles: set = field(default_factory=set)
    is_system_admin: bool = False


@dataclass
class DocumentACL:
    """Document-level authorization metadata (knowledge_fabric.document_acl)."""
    document_id: str
    owner_user_id: str
    tier: KnowledgeTier = KnowledgeTier.SECONDARY
    visibility: Visibility = Visibility.PRIVATE
    space_id: str = None
    readers: set = field(default_factory=set)
    editors: set = field(default_factory=set)
    managers: set = field(default_factory=set)
    group_readers: set = field(default_factory=set)
