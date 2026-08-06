"""Tests for the Legal Analysis Engine (17O-C).

Covers: schema validation, immutable storage (append-only),
audit trail (trigger-based), FTS5 search, constitution cross-reference,
content integrity verification.
"""

import pytest
from core.legal.schema import (LegalDocument, DocumentStatus, DocumentType,
                                validate_document)
from core.legal.engine import LegalEngine


@pytest.fixture
def engine():
    e = LegalEngine(":memory:")
    e.connect()
    yield e
    e.close()


class TestSchemaValidation:
    def test_valid_document(self):
        doc = LegalDocument(
            jurisdiction="ghana-supreme-court", court="Supreme Court of Ghana",
            year=2024, citation="[2024] GHASC 42")
        assert validate_document(doc) == []

    def test_missing_required_fields(self):
        doc = LegalDocument(jurisdiction="", court="", year=0, citation="")
        assert len(validate_document(doc)) == 4

    def test_invalid_status(self):
        doc = LegalDocument(jurisdiction="ghana-supreme-court",
            court="Supreme Court", year=2024, citation="[2024] GHASC 42",
            status="hello")
        errors = validate_document(doc)
        assert any("status" in e for e in errors)

    def test_invalid_jurisdiction(self):
        doc = LegalDocument(jurisdiction="france", court="Some Court",
            year=2024, citation="foo")
        errors = validate_document(doc)
        assert any("jurisdiction" in e for e in errors)

    def test_year_not_int(self):
        doc = LegalDocument(jurisdiction="ghana-supreme-court",
            court="Supreme Court", year="not-a-number", citation="foo")
        errors = validate_document(doc)
        assert any("year" in e for e in errors)

    def test_document_status_enum_values(self):
        assert DocumentStatus.CURRENT.value == "current"
        assert DocumentStatus.OVERRULED.value == "overruled"
        assert DocumentStatus.AMENDED.value == "amended"


class TestStorage:
    def test_insert_and_retrieve(self, engine):
        doc = LegalDocument(jurisdiction="ghana-supreme-court",
            court="Supreme Court of Ghana", year=2024, citation="[2024] GHASC 42",
            judge="Torkornoo CJ")
        doc_id = engine.ingest(doc, "Full text of judgment.")
        retrieved = engine.get(doc_id)
        assert retrieved is not None
        assert retrieved["citation"] == "[2024] GHASC 42"
        assert retrieved["content"] == "Full text of judgment."
        assert retrieved["version_number"] == 1

    def test_versioning(self, engine):
        doc = LegalDocument(jurisdiction="ghana-high-court",
            court="High Court, Accra", year=2024, citation="[2024] GHAHC 15")
        doc_id = engine.ingest(doc, "Version 1 content.")
        engine.update(doc_id, new_content="Version 2 content.")
        engine.update(doc_id, new_content="Version 3 content.")
        versions = engine.list_versions(doc_id)
        assert len(versions) == 3
        assert versions[0]["version_number"] == 3
        assert versions[2]["version_number"] == 1
        v1 = engine.get_version(doc_id, 1)
        assert v1["content"] == "Version 1 content."
        v3 = engine.get_version(doc_id, 3)
        assert v3["content"] == "Version 3 content."

    def test_immutable_storage(self, engine):
        doc = LegalDocument(jurisdiction="ghana-parliament",
            court="Parliament of Ghana", year=2024, citation="Act 1234")
        doc_id = engine.ingest(doc, "Original text.")
        engine.update(doc_id, new_content="Amended text.")
        engine.update(doc_id, new_content="Further amended text.")
        v1 = engine.get_version(doc_id, 1)
        assert v1["content"] == "Original text."
        v2 = engine.get_version(doc_id, 2)
        assert v2["content"] == "Amended text."

    def test_audit_trail(self, engine):
        doc = LegalDocument(jurisdiction="ghana-supreme-court",
            court="Supreme Court of Ghana", year=2024, citation="[2024] GHASC 99")
        doc_id = engine.ingest(doc, "First content.")
        engine.update(doc_id, new_content="Second content.")
        audit = engine.audit_log(doc_id=doc_id)
        assert len(audit) == 2
        assert all(e["action"] == "create" for e in audit)
        assert all(e["document_id"] == doc_id for e in audit)

    def test_content_integrity_verification(self, engine):
        doc = LegalDocument(jurisdiction="ghana-court-of-appeal",
            court="Court of Appeal", year=2024, citation="[2024] GHACA 10")
        doc_id = engine.ingest(doc, "Content A")
        engine.update(doc_id, new_content="Content B")
        result = engine.verify_integrity(doc_id)
        assert result["all_versions_intact"] is True
        assert len(result["versions"]) == 2

    def test_update_metadata_only(self, engine):
        doc = LegalDocument(jurisdiction="ghana-high-court",
            court="High Court", year=2024, citation="[2024] GHAHC 50",
            judge="Original Judge")
        doc_id = engine.ingest(doc, "Some content.")
        engine.update(doc_id, metadata={"judge": "New Judge"})
        retrieved = engine.get(doc_id)
        assert retrieved["judge"] == "New Judge"
        assert retrieved["content"] == "Some content."
        assert retrieved["version_number"] == 1


class TestSearch:
    @pytest.fixture(autouse=True)
    def seed(self, engine):
        docs = [
            (LegalDocument(jurisdiction="ghana-supreme-court",
                court="Supreme Court of Ghana", year=2024,
                citation="[2024] GHASC 1", title="Republic v A"),
             "The defendant argued Article 15 of the 1992 Constitution."),
            (LegalDocument(jurisdiction="ghana-high-court",
                court="High Court, Kumasi", year=2023,
                citation="[2023] GHAHC 88", title="X v Y"),
             "Land dispute in the Ashanti Region under customary law."),
            (LegalDocument(jurisdiction="ghana-court-of-appeal",
                court="Court of Appeal", year=2024,
                citation="[2024] GHACA 55", title="State v B"),
             "Appeal raised Article 19 fair trial rights under 1992 Constitution."),
        ]
        for d, content in docs:
            engine.ingest(d, content)

    def test_full_text_search(self, engine):
        results = engine.search("Constitution")
        assert len(results) >= 2

    def test_full_text_search_no_match(self, engine):
        assert engine.search("xyznonexistent") == []

    def test_by_jurisdiction_filter(self, engine):
        results = engine.search_by_jurisdiction("ghana-supreme-court")
        assert len(results) == 1
        assert results[0]["citation"] == "[2024] GHASC 1"

    def test_by_constitution_ref(self, engine):
        engine.add_constitution_ref(1, "15", "")
        engine.add_constitution_ref(3, "19", "")
        engine.add_constitution_ref(1, "17", "")
        results = engine.search_by_constitution("15")
        assert len(results) == 1
        assert results[0]["id"] == 1
        articles = engine.constitution_articles()
        assert set(articles) == {"15", "17", "19"}

    def test_by_status(self, engine):
        doc = LegalDocument(jurisdiction="ghana-supreme-court",
            court="Supreme Court of Ghana", year=2020,
            citation="[2020] GHASC 7", status="overruled")
        engine.ingest(doc, "Overruled content.")
        from core.legal.search import LegalSearch
        s = LegalSearch(engine.storage)
        overruled = s.by_status("overruled")
        assert any(d["citation"] == "[2020] GHASC 7" for d in overruled)

    def test_by_year_range(self, engine):
        from core.legal.search import LegalSearch
        s = LegalSearch(engine.storage)
        results = s.by_year_range(2024, 2024)
        assert all(d["year"] == 2024 for d in results)
        assert len(results) >= 2


class TestListDocuments:
    def test_list_all(self, engine):
        doc = LegalDocument(jurisdiction="ghana-supreme-court",
            court="Supreme Court", year=2024, citation="[2024] GHASC 100")
        engine.ingest(doc, "Content.")
        results = engine.list_documents()
        assert len(results) >= 1

    def test_list_with_pagination(self, engine):
        for i in range(5):
            doc = LegalDocument(jurisdiction="ghana-high-court",
                court=f"Court {i}", year=2024, citation=f"[2024] GHAHC {i}")
            engine.ingest(doc, f"Content {i}")
        page1 = engine.list_documents(limit=2, offset=0)
        page2 = engine.list_documents(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        ids_page1 = {d["id"] for d in page1}
        ids_page2 = {d["id"] for d in page2}
        assert ids_page1.isdisjoint(ids_page2)
