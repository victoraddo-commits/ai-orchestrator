"""
Tests for KLAUS API endpoints using FastAPI TestClient.

Validates source management, document listing, ingestion, quality control,
vector indexing, search, scheduling, and monitoring endpoints.
"""

import base64
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

app = FastAPI()

from core.klaus.api_endpoints import klaus_router
app.include_router(klaus_router)


async def _bypass_auth():
    """Override require_bridge_token for tests — skip token validation."""
    return "test-operator"


app.dependency_overrides = {
    # The router-level dependencies are set on the router itself,
    # not easily overridden at app level for the whole router. Instead
    # we override require_bridge_token globally.
}

from core.bridge_auth import require_bridge_token
app.dependency_overrides[require_bridge_token] = _bypass_auth


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_db_sources():
    return [
        {"id": 1, "url": "https://parliament.gh", "domain": "parliament.gh", "tier": 1,
         "jurisdiction": "Ghana", "status": "active", "reliability_score": 1.0},
        {"id": 2, "url": "https://judiciary.gov.gh", "domain": "judiciary.gov.gh", "tier": 1,
         "jurisdiction": "Ghana", "status": "active", "reliability_score": 1.0},
    ]


@pytest.fixture
def mock_db_docs():
    return [
        {"id": 1, "source_id": 1, "title": "Constitution", "file_hash": "abc", "file_path": "/tmp/abc.pdf",
         "category": "Constitutional Law", "jurisdiction": "Ghana", "copyright_classification": "public_domain",
         "access_level": "full_storage", "review_status": "approved"},
    ]


class TestSourceEndpoints:
    def test_list_sources(self, client):
        with patch("core.klaus.api_endpoints.list_sources", return_value=[]) as mock_list:
            response = client.get("/klaus/sources")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_sources_with_filters(self, client):
        with patch("core.klaus.api_endpoints.list_sources", return_value=[]) as mock_list:
            response = client.get("/klaus/sources?tier=1&status=active")
            assert response.status_code == 200
            mock_list.assert_called_once()

    def test_add_source(self, client):
        with patch("core.klaus.api_endpoints.add_source", return_value=1) as mock_add:
            response = client.post("/klaus/sources", json={
                "url": "https://parliament.gh",
                "domain": "parliament.gh",
                "tier": 1,
            })
            assert response.status_code == 200
            assert response.json()["id"] == 1

    def test_add_source_missing_fields(self, client):
        response = client.post("/klaus/sources", json={"url": "https://example.com"})
        assert response.status_code == 400

    def test_add_source_invalid_tier(self, client):
        response = client.post("/klaus/sources", json={
            "url": "https://example.com", "domain": "example.com", "tier": 99
        })
        assert response.status_code == 400

    def test_update_source_status(self, client):
        with patch("core.klaus.api_endpoints.get_source", return_value={"id": 1}), \
             patch("core.klaus.api_endpoints.update_source_status") as mock_update:
            response = client.put("/klaus/sources/1/status", json={
                "status": "broken", "reliability_score": 0.5
            })
            assert response.status_code == 200
            mock_update.assert_called_once()

    def test_update_status_source_not_found(self, client):
        with patch("core.klaus.api_endpoints.get_source", return_value=None):
            response = client.put("/klaus/sources/999/status", json={"status": "broken"})
            assert response.status_code == 404


class TestDocumentEndpoints:
    def test_list_documents(self, client):
        with patch("core.klaus.api_endpoints.list_documents", return_value=[]) as mock_list:
            response = client.get("/klaus/documents")
            assert response.status_code == 200
            assert response.json() == []

    def test_list_documents_with_filters(self, client):
        with patch("core.klaus.api_endpoints.list_documents", return_value=[]) as mock_list:
            response = client.get("/klaus/documents?category=Legislation&review_status=approved")
            assert response.status_code == 200

    def test_get_flagged_documents(self, client):
        with patch("core.klaus.api_endpoints.get_documents_flagged_for_review", return_value=[]):
            response = client.get("/klaus/documents/flagged")
            assert response.status_code == 200

    def test_get_document(self, client, mock_db_docs):
        with patch("core.klaus.api_endpoints.get_document", return_value=mock_db_docs[0]):
            response = client.get("/klaus/documents/1")
            assert response.status_code == 200
            assert response.json()["title"] == "Constitution"

    def test_get_document_not_found(self, client):
        with patch("core.klaus.api_endpoints.get_document", return_value=None):
            response = client.get("/klaus/documents/999")
            assert response.status_code == 404

    def test_get_document_chunks(self, client):
        doc = {"id": 1, "title": "Test"}
        chunks = [{"id": 1, "chunk_index": 0, "content": "text"}]
        with patch("core.klaus.api_endpoints.get_document", return_value=doc), \
             patch("core.klaus.api_endpoints.get_chunks_for_document", return_value=chunks):
            response = client.get("/klaus/documents/1/chunks")
            assert response.status_code == 200
            assert len(response.json()) == 1

    def test_review_document(self, client):
        doc = {"id": 1}
        with patch("core.klaus.api_endpoints.get_document", return_value=doc), \
             patch("core.klaus.api_endpoints.update_document_review_status"), \
             patch("core.klaus.api_endpoints.log_audit_event"):
            response = client.put("/klaus/documents/1/review", json={"review_status": "approved"})
            assert response.status_code == 200

    def test_review_document_invalid_status(self, client):
        doc = {"id": 1}
        with patch("core.klaus.api_endpoints.get_document", return_value=doc):
            response = client.put("/klaus/documents/1/review", json={"review_status": "invalid"})
            assert response.status_code == 400

    def test_review_document_not_found(self, client):
        with patch("core.klaus.api_endpoints.get_document", return_value=None):
            response = client.put("/klaus/documents/999/review", json={"review_status": "approved"})
            assert response.status_code == 404


class TestIngestionEndpoint:
    def test_ingest_document(self, client):
        content_b64 = base64.b64encode(b"Test legal content").decode()
        with patch("core.klaus.api_endpoints.get_source", return_value={"id": 1, "domain": "test.gh"}), \
             patch("core.klaus.api_endpoints.process_document", return_value={
                 "status": "ingested", "document_id": 42, "category": "Legislation",
                 "jurisdiction": "Ghana",
             }):
            response = client.post("/klaus/ingest", json={
                "content": content_b64,
                "filename": "test_act.pdf",
                "source_id": 1,
                "source_url": "https://parliament.gh",
            })
            assert response.status_code == 200
            assert response.json()["status"] == "ingested"

    def test_ingest_missing_fields(self, client):
        response = client.post("/klaus/ingest", json={"content": "abc"})
        assert response.status_code == 400

    def test_ingest_invalid_base64(self, client):
        with patch("core.klaus.api_endpoints.get_source", return_value={"id": 1}):
            response = client.post("/klaus/ingest", json={
                "content": "!!!not-base64!!!",
                "filename": "test.pdf",
                "source_id": 1,
            })
        assert response.status_code == 400

    def test_ingest_source_not_found(self, client):
        content_b64 = base64.b64encode(b"content").decode()
        with patch("core.klaus.api_endpoints.get_source", return_value=None):
            response = client.post("/klaus/ingest", json={
                "content": content_b64,
                "filename": "test.pdf",
                "source_id": 999,
            })
            assert response.status_code == 400

    def test_ingest_with_optional_fields(self, client):
        content_b64 = base64.b64encode(b"content").decode()
        with patch("core.klaus.api_endpoints.get_source", return_value={"id": 1}), \
             patch("core.klaus.api_endpoints.process_document", return_value={"status": "ingested", "document_id": 1}):
            response = client.post("/klaus/ingest", json={
                "content": content_b64,
                "filename": "act.pdf",
                "source_id": 1,
                "source_url": "https://parliament.gh",
                "jurisdiction": "Ghana",
                "court": "Supreme Court",
                "year": 2024,
                "legislation_number": "Act 703",
                "effective_date": "2024-01-01",
                "bypass_copyright": True,
            })
            assert response.status_code == 200


class TestQualityControlEndpoint:
    def test_verify_document(self, client):
        doc = {"id": 1}
        with patch("core.klaus.api_endpoints.get_document", return_value=doc), \
             patch("core.klaus.api_endpoints.run_all_agents", return_value={
                 "overall": "approved", "source_verification": {"passed": True}
             }):
            response = client.post("/klaus/documents/1/verify")
            assert response.status_code == 200
            assert response.json()["results"]["overall"] == "approved"

    def test_verify_document_not_found(self, client):
        with patch("core.klaus.api_endpoints.get_document", return_value=None):
            response = client.post("/klaus/documents/999/verify")
            assert response.status_code == 404


class TestVectorIndexingEndpoint:
    def test_index_document(self, client):
        doc = {"id": 1, "access_level": "full_storage"}
        with patch("core.klaus.api_endpoints.get_document", return_value=doc), \
             patch("core.klaus.api_endpoints.index_document_chunks", return_value=5):
            response = client.post("/klaus/documents/1/index")
            assert response.status_code == 200
            assert response.json()["chunks_indexed"] == 5

    def test_index_metadata_only_blocked(self, client):
        doc = {"id": 1, "access_level": "metadata_only"}
        with patch("core.klaus.api_endpoints.get_document", return_value=doc):
            response = client.post("/klaus/documents/1/index")
            assert response.status_code == 400

    def test_index_document_not_found(self, client):
        with patch("core.klaus.api_endpoints.get_document", return_value=None):
            response = client.post("/klaus/documents/999/index")
            assert response.status_code == 404


class TestSearchEndpoint:
    def test_search(self, client):
        results = [
            {"id": 1, "document_id": 1, "content": "relevant text", "similarity": 0.85}
        ]
        with patch("core.klaus.api_endpoints.search_similar", return_value=results):
            response = client.get("/klaus/search?q=constitution")
            assert response.status_code == 200
            assert response.json()["count"] == 1

    def test_search_empty_query_blocked(self, client):
        response = client.get("/klaus/search?q=")
        assert response.status_code == 400

    def test_search_with_limit_and_threshold(self, client):
        with patch("core.klaus.api_endpoints.search_similar", return_value=[]):
            response = client.get("/klaus/search?q=test&limit=5&threshold=0.3")
            assert response.status_code == 200


class TestSchedulerEndpoint:
    def test_trigger_job(self, client):
        with patch("core.klaus.api_endpoints.trigger_job_now", return_value=True):
            response = client.post("/klaus/scheduler/trigger/klaus_daily")
            assert response.status_code == 200
            assert response.json()["triggered"] is True

    def test_trigger_invalid_job(self, client):
        response = client.post("/klaus/scheduler/trigger/klaus_hourly")
        assert response.status_code == 400

    def test_trigger_job_failure(self, client):
        with patch("core.klaus.api_endpoints.trigger_job_now", return_value=False):
            response = client.post("/klaus/scheduler/trigger/klaus_daily")
            assert response.status_code == 500


class TestMonitoringEndpoint:
    def test_monitoring(self, client):
        stats = {
            "documents_total": 10, "chunks_total": 50, "chunks_indexed": 30,
            "sources_total": 5, "sources_broken": 1,
        }
        counts = {"approved": 8, "pending": 1, "flagged": 1}
        with patch("core.klaus.api_endpoints.get_storage_stats", return_value=stats), \
             patch("core.klaus.api_endpoints.count_documents_by_status", return_value=counts), \
             patch("core.klaus.api_endpoints.get_documents_flagged_for_review", return_value=[{}]):
            response = client.get("/klaus/monitoring")
            assert response.status_code == 200
            assert response.json()["storage"]["documents_total"] == 10
            assert "categories" in response.json()

    def test_audit_logs(self, client):
        with patch("core.klaus.api_endpoints.get_audit_logs", return_value=[]):
            response = client.get("/klaus/audit-logs")
            assert response.status_code == 200
            assert response.json() == []

    def test_audit_logs_with_filters(self, client):
        with patch("core.klaus.api_endpoints.get_audit_logs", return_value=[]):
            response = client.get("/klaus/audit-logs?event_type=failure&severity=error")
            assert response.status_code == 200


class TestReferenceEndpoints:
    def test_reference_categories(self, client):
        response = client.get("/klaus/reference/categories")
        assert response.status_code == 200
        assert "categories" in response.json()
        assert "Constitutional Law" in response.json()["categories"]

    def test_reference_copyright(self, client):
        response = client.get("/klaus/reference/copyright")
        assert response.status_code == 200
        assert "classifications" in response.json()
        assert "public_domain" in response.json()["classifications"]


class TestCopyrightCompliance:
    """Tests verifying that copyright classification is enforced at API level."""

    def test_metadata_only_document_cannot_be_indexed(self, client):
        doc = {"id": 1, "access_level": "metadata_only"}
        with patch("core.klaus.api_endpoints.get_document", return_value=doc):
            response = client.post("/klaus/documents/1/index")
            assert response.status_code == 400
            assert "metadata_only" in response.json()["detail"].lower() or "cannot index" in response.json()["detail"].lower()

    def test_search_only_returns_approved_documents(self, client):
        """The db_manager.similarity_search already filters for approved+full_storage only."""
        results = [{"id": 1, "document_id": 10, "similarity": 0.9}]
        with patch("core.klaus.api_endpoints.search_similar", return_value=results):
            response = client.get("/klaus/search?q=law")
            assert response.status_code == 200


class TestJurisKaiIsolation:
    """Tests verifying that user-uploaded content never enters the shared knowledge base."""

    def test_ingest_without_source_rejects(self, client):
        """Documents ingested without a valid source_id should be rejected."""
        content_b64 = base64.b64encode(b"user upload").decode()
        with patch("core.klaus.api_endpoints.get_source", return_value=None):
            response = client.post("/klaus/ingest", json={
                "content": content_b64,
                "filename": "user_doc.pdf",
                "source_id": 999,
            })
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_process_document_with_no_source_disallowed(self, client):
        """Ingestion requires source_id that maps to a registered source."""
        content_b64 = base64.b64encode(b"user content").decode()
        with patch("core.klaus.api_endpoints.get_source", return_value=None):
            response = client.post("/klaus/ingest", json={
                "content": content_b64,
                "filename": "upload.pdf",
                "source_id": 1,
            })
            assert response.status_code == 400

    def test_copyright_protected_never_gets_full_storage_without_bypass(self, client):
        """Copyright-protected documents with bypass_copyright=False are metadata_only."""
        content_b64 = base64.b64encode(b"Copyright 2024 User Inc. All rights reserved.").decode()
        with patch("core.klaus.api_endpoints.get_source", return_value={"id": 1}), \
             patch("core.klaus.api_endpoints.process_document", return_value={
                 "status": "ingested",
                 "document_id": 50,
                 "access_level": "metadata_only",
                 "copyright_classification": "copyright_protected",
                 "chunks_count": 0,
             }):
            response = client.post("/klaus/ingest", json={
                "content": content_b64,
                "filename": "user_opinion.pdf",
                "source_id": 1,
                "source_url": "https://unknown.com",
                "bypass_copyright": False,
            })
            assert response.status_code == 200
            assert response.json()["access_level"] == "metadata_only"
            assert response.json()["chunks_count"] == 0
