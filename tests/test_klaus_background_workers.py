"""
Tests for KLAUS background workers.

Tests discovery, download, and processing functions
with mocked external dependencies.
"""

from unittest.mock import patch, MagicMock
import pytest

from core.klaus.background_workers import (
    process_discovered_documents,
)


class TestProcessDiscoveredDocuments:
    def test_processes_all_docs(self):
        import core.klaus.background_workers as bw
        with patch.object(bw, "download_document_content", return_value=(b"pdf content", "doc1.pdf")), \
             patch.object(bw, "process_document", return_value={
                 "status": "ingested", "document_id": 42,
             }), \
             patch.object(bw, "run_all_agents", return_value={"overall": "approved"}), \
             patch.object(bw, "index_document_chunks", return_value=3):

            docs = [
                {"title": "Doc 1", "url": "https://example.com/doc1.pdf",
                 "type": "pdf", "source_domain": "example.com"},
            ]

            count = process_discovered_documents(1, "https://example.com", "example.com", docs)
            assert count == 1

    def test_skips_duplicate_docs(self):
        import core.klaus.background_workers as bw
        with patch.object(bw, "download_document_content", return_value=(b"pdf content", "doc1.pdf")), \
             patch.object(bw, "process_document", return_value={"status": "duplicate", "document_id": 10}):

            docs = [
                {"title": "Doc 1", "url": "https://example.com/doc1.pdf",
                 "type": "pdf", "source_domain": "example.com"},
            ]

            count = process_discovered_documents(1, "https://example.com", "example.com", docs)
            assert count == 0

    def test_handles_download_failure(self):
        import core.klaus.background_workers as bw
        with patch.object(bw, "download_document_content", return_value=None):

            docs = [
                {"title": "Doc 1", "url": "https://example.com/doc1.pdf",
                 "type": "pdf", "source_domain": "example.com"},
            ]

            count = process_discovered_documents(1, "https://example.com", "example.com", docs)
            assert count == 0

    def test_handles_process_error(self):
        import core.klaus.background_workers as bw
        with patch.object(bw, "download_document_content", return_value=(b"pdf", "doc1.pdf")), \
             patch.object(bw, "process_document", side_effect=RuntimeError("DB error")):

            docs = [
                {"title": "Doc 1", "url": "https://example.com/doc1.pdf",
                 "type": "pdf", "source_domain": "example.com"},
            ]

            count = process_discovered_documents(1, "https://example.com", "example.com", docs)
            assert count == 0


class TestDiscoverSourceContent:
    def test_discovers_pdf_links(self):
        import core.klaus.background_workers as bw
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html><body>
            <a href="doc1.pdf">Act 123</a>
            <a href="doc2.pdf">Act 456</a>
        </body></html>
        """
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            docs = bw.discover_source_content("https://example.com", "example.com")
            assert len(docs) == 2
            assert docs[0]["title"] == "Act 123"
            assert docs[0]["type"] == "pdf"
            assert docs[1]["title"] == "Act 456"

    def test_resolves_relative_urls(self):
        import core.klaus.background_workers as bw
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html><body>
            <a href="/docs/act.pdf">Act</a>
        </body></html>
        """
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            docs = bw.discover_source_content("https://example.com", "example.com")
            assert len(docs) == 1
            assert docs[0]["url"] == "https://example.com/docs/act.pdf"

    def test_discovers_txt_links(self):
        import core.klaus.background_workers as bw
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html><body>
            <a href="notes.txt">Meeting Notes</a>
        </body></html>
        """
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            docs = bw.discover_source_content("https://example.com", "example.com")
            assert len(docs) == 1
            assert docs[0]["type"] == "txt"

    def test_skips_anchor_links(self):
        import core.klaus.background_workers as bw
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html><body>
            <a href="#section1">Jump</a>
            <a href="real.pdf">Real Doc</a>
        </body></html>
        """
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            docs = bw.discover_source_content("https://example.com", "example.com")
            assert len(docs) == 1
            assert docs[0]["url"].endswith("real.pdf")

    def test_handles_http_error(self):
        import core.klaus.background_workers as bw
        with patch("requests.get", side_effect=RuntimeError("Connection failed")):
            docs = bw.discover_source_content("https://broken.gh", "broken.gh")
            assert docs == []


class TestDownloadDocumentContent:
    def test_downloads_pdf(self):
        import core.klaus.background_workers as bw
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"%PDF-1.4 test"
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = bw.download_document_content("https://example.com/doc.pdf")
            assert result is not None
            content, filename = result
            assert content == b"%PDF-1.4 test"
            assert filename == "doc.pdf"

    def test_handles_download_failure(self):
        import core.klaus.background_workers as bw
        with patch("requests.get", side_effect=RuntimeError("Timeout")):
            result = bw.download_document_content("https://example.com/doc.pdf")
            assert result is None

    def test_handles_unknown_filename(self):
        import core.klaus.background_workers as bw
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"test"
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = bw.download_document_content("https://example.com/")
            assert result is not None
            content, filename = result
            assert filename == "unnamed_document"
