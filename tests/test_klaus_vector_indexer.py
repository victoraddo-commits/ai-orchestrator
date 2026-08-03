"""
Tests for KLAUS vector indexer.

Tests embedding generation, chunk indexing, similarity search,
and storage stats without needing PostgreSQL or sentence-transformers.
"""

from unittest.mock import patch, MagicMock, call
import pytest

from core.klaus.vector_indexer import (
    generate_embedding,
    generate_embeddings,
    index_document_chunks,
    search_similar,
    get_storage_stats,
    MODEL_NAME,
    EMBEDDING_DIM,
)


class TestEmbeddingGeneration:
    def test_generate_embedding_returns_list_of_floats(self):
        import core.klaus.vector_indexer as vi
        vi._embedding_model = None

        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1, 0.2, 0.3]

        with patch("sentence_transformers.SentenceTransformer", return_value=mock_model):
            result = generate_embedding("test text")
            assert isinstance(result, list)
            assert len(result) == 3
            assert all(isinstance(x, float) for x in result)

    def test_generate_embeddings_batch(self):
        import core.klaus.vector_indexer as vi
        vi._embedding_model = None

        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [[0.1, 0.2], [0.3, 0.4]]

        with patch("sentence_transformers.SentenceTransformer", return_value=mock_model):
            result = generate_embeddings(["text1", "text2"])
            assert len(result) == 2
            assert len(result[0]) == 2

    def test_model_cached_after_first_load(self):
        import core.klaus.vector_indexer as vi
        vi._embedding_model = None

        mock_model = MagicMock()
        with patch("sentence_transformers.SentenceTransformer", return_value=mock_model) as mock_st:
            generate_embedding("first call")
            generate_embedding("second call")
            assert mock_st.call_count == 1


class TestIndexDocumentChunks:
    def test_indexes_all_chunks(self):
        import core.klaus.vector_indexer as vi
        from contextlib import contextmanager

        mock_cur = MagicMock()
        mock_conn = MagicMock()

        @contextmanager
        def mock_get_cursor():
            yield mock_cur

        with patch.object(vi, "get_cursor", side_effect=mock_get_cursor), \
             patch.object(vi, "get_chunks_for_document") as mock_get, \
             patch.object(vi, "generate_embeddings") as mock_gen, \
             patch.object(vi, "log_audit_event"):

            mock_get.return_value = [
                {"id": 1, "content": "chunk one"},
                {"id": 2, "content": "chunk two"},
                {"id": 3, "content": "chunk three"},
            ]
            mock_gen.return_value = [[0.1]*384, [0.2]*384, [0.3]*384]

            result = index_document_chunks(42)
            assert result == 3

    def test_returns_zero_for_no_chunks(self):
        import core.klaus.vector_indexer as vi
        with patch.object(vi, "get_chunks_for_document", return_value=[]):
            result = index_document_chunks(42)
            assert result == 0

    def test_handles_update_failure_gracefully(self):
        import core.klaus.vector_indexer as vi
        from contextlib import contextmanager

        mock_cur = MagicMock()
        mock_cur.execute.side_effect = [
            Exception("DB error"), None, Exception("DB error 2"),
        ]

        @contextmanager
        def mock_get_cursor():
            yield mock_cur

        with patch.object(vi, "get_cursor", side_effect=mock_get_cursor), \
             patch.object(vi, "get_chunks_for_document") as mock_get, \
             patch.object(vi, "generate_embeddings") as mock_gen, \
             patch.object(vi, "log_audit_event"):

            mock_get.return_value = [
                {"id": 1, "content": "chunk one"},
                {"id": 2, "content": "chunk two"},
                {"id": 3, "content": "chunk three"},
            ]
            mock_gen.return_value = [[0.1]*384, [0.2]*384, [0.3]*384]

            result = index_document_chunks(42)
            assert result == 1


class TestSearchSimilar:
    def test_returns_results(self):
        import core.klaus.vector_indexer as vi
        vi._embedding_model = None
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1] * 384

        with patch("sentence_transformers.SentenceTransformer", return_value=mock_model), \
             patch.object(vi, "similarity_search", return_value=[
                 {"id": 1, "content": "result text", "similarity": 0.95},
             ]):
            results = search_similar("constitutional law", limit=5, threshold=0.7)
            assert len(results) == 1
            assert results[0]["similarity"] == 0.95

    def test_returns_empty_list_on_error(self):
        import core.klaus.vector_indexer as vi
        vi._embedding_model = None
        with patch("sentence_transformers.SentenceTransformer", side_effect=RuntimeError("model not loaded")):
            results = search_similar("test", limit=5)
            assert results == []


class TestGetStorageStats:
    def test_returns_stats_on_success(self):
        import core.klaus.vector_indexer as vi
        from contextlib import contextmanager

        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            {"ct": 10}, {"ct": 50}, {"ct": 30}, {"ct": 3}, {"ct": 1},
        ]

        @contextmanager
        def mock_get_cursor():
            yield mock_cur

        with patch.object(vi, "get_cursor", side_effect=mock_get_cursor):
            stats = get_storage_stats()
            assert stats["documents_total"] == 10
            assert stats["chunks_total"] == 50
            assert stats["chunks_indexed"] == 30
            assert stats["sources_total"] == 3
            assert stats["sources_broken"] == 1
            assert stats["embedding_model"] == MODEL_NAME
            assert stats["embedding_dim"] == EMBEDDING_DIM

    def test_returns_error_dict_on_failure(self):
        import core.klaus.vector_indexer as vi
        with patch.object(vi, "get_cursor", side_effect=RuntimeError("DB connection failed")):
            stats = get_storage_stats()
            assert "error" in stats


def test_embedding_dimension_constant():
    assert EMBEDDING_DIM == 384
