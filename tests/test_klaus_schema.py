"""
Tests for KLAUS schema constants and validation.

Tests that the schema constants (categories, classifications, etc.)
are consistent and match the approved plan.
"""

from core.klaus.schema import (
    KNOWLEDGE_CATEGORIES,
    COPYRIGHT_CLASSIFICATIONS,
    EVENT_TYPES,
    SEVERITY_LEVELS,
    SOURCE_TIERS,
)


def test_knowledge_categories_match_approved_plan():
    categories = set(KNOWLEDGE_CATEGORIES)
    expected = {
        "Constitutional Law",
        "Legislation",
        "Judiciary",
        "Legal Procedure",
        "International Law",
        "Legal Scholarship",
    }
    assert categories == expected, f"Categories mismatch: {categories.symmetric_difference(expected)}"


def test_copyright_classifications_are_five():
    assert len(COPYRIGHT_CLASSIFICATIONS) == 5
    expected = {
        "public_domain",
        "open_license",
        "official_public_access",
        "copyright_protected",
        "unknown",
    }
    assert set(COPYRIGHT_CLASSIFICATIONS) == expected


def test_event_types_cover_lifecycle():
    expected = {
        "discovery",
        "download",
        "verification",
        "classification",
        "failure",
        "review",
    }
    assert set(EVENT_TYPES) == expected


def test_severity_levels_match_standard():
    expected = {"info", "warning", "error", "critical"}
    assert set(SEVERITY_LEVELS) == expected


def test_source_tiers_are_three():
    assert sorted(SOURCE_TIERS.keys()) == [1, 2, 3]
    assert SOURCE_TIERS[1] == "official_government"
    assert SOURCE_TIERS[2] == "recognized_institution"
    assert SOURCE_TIERS[3] == "secondary"


def test_schema_sql_contains_all_tables():
    from core.klaus.schema import SCHEMA_SQL

    tables = ["klaus_sources", "klaus_documents", "klaus_document_chunks", "klaus_audit_logs"]
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA_SQL, f"Missing table: {table}"


def test_schema_sql_contains_indexes():
    from core.klaus.schema import SCHEMA_SQL

    indexes = [
        "idx_klaus_sources_domain",
        "idx_klaus_sources_status",
        "idx_klaus_docs_file_hash",
        "idx_klaus_docs_review_status",
        "idx_klaus_docs_category",
        "idx_klaus_docs_source_id",
        "idx_klaus_chunks_doc_id",
        "idx_klaus_audit_doc_id",
        "idx_klaus_audit_event_type",
    ]
    for idx in indexes:
        assert f"CREATE INDEX IF NOT EXISTS {idx}" in SCHEMA_SQL, f"Missing index: {idx}"


def test_schema_sql_has_pgvector_extension():
    from core.klaus.schema import SCHEMA_SQL
    assert 'CREATE EXTENSION IF NOT EXISTS "pgvector"' in SCHEMA_SQL


def test_schema_sql_has_embedding_column():
    from core.klaus.schema import SCHEMA_SQL
    assert "embedding VECTOR(384)" in SCHEMA_SQL
