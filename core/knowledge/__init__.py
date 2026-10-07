"""KAI Knowledge Fabric — Primary + Secondary knowledge with permission-aware
retrieval (§14). Reuses KLAUS (PostgreSQL/pgvector) as the backend.

This package currently provides the model + authorization core; the storage/
ingestion/retrieval/API layers build on it (see docs/KNOWLEDGE_FABRIC_PLAN.md).
"""
from core.knowledge.model import (
    DocRole,
    DocumentACL,
    KnowledgeTier,
    Principal,
    Visibility,
)
from core.knowledge.authz import (
    can_edit,
    can_manage,
    can_read,
    filter_readable,
    readable_document_ids,
)

__all__ = [
    "DocRole",
    "DocumentACL",
    "KnowledgeTier",
    "Principal",
    "Visibility",
    "can_edit",
    "can_manage",
    "can_read",
    "filter_readable",
    "readable_document_ids",
]
