"""API-level /search mode routing (Phase 1 T3).

Exercises ``legal_brain_api.search_dispatch`` directly (no HTTP server): the
``hybrid`` mode must fuse BM25 + dense and degrade to BM25 when the dense side
is unavailable, unknown modes fall back to ``or``, and existing modes keep
working while gaining authority annotations.
"""
import sqlite3

from core.legal.embeddings import EmbeddingError
import legal_brain_api as api


class FakeStorage:
    def __init__(self, rows):
        self.rows = rows
        self.conn = sqlite3.connect(":memory:")   # no document_meta table

    def search(self, query, limit=20, mode="or", commercial=False):
        return self.rows[:limit]


class FakeEmbedIndex:
    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error

    def search(self, query, limit=10):
        if self.error is not None:
            raise self.error
        return self.hits[:limit]


def _bm25_row(doc_id=1):
    return {"id": doc_id, "title": "Criminal Offences Act", "snippet": "x",
            "store_mode": "full", "type": "act", "year": 1960,
            "citation": "Act 29"}


def _dense_hit(doc_id=1):
    return {"document_id": doc_id, "title": "Criminal Offences Act",
            "score": 0.82, "snippet": "y", "chunk_index": 0, "char_start": 0,
            "char_end": 1, "type": "act", "year": 1960, "citation": "Act 29"}


def test_hybrid_mode_routes_to_hybrid_search():
    st = FakeStorage([_bm25_row()])
    idx = FakeEmbedIndex([_dense_hit()])
    results, mode = api.search_dispatch("Act 29", mode="hybrid", limit=5,
                                        storage=st, embed_index=idx)
    assert mode == "hybrid"
    assert results and results[0]["doc_id"] == 1
    assert results[0]["authority_level"] == "act"
    assert "confidence" in results[0]
    assert "status" in results[0]


def test_hybrid_mode_degrades_when_dense_fails():
    st = FakeStorage([_bm25_row()])
    idx = FakeEmbedIndex(error=EmbeddingError("tunnel down"))
    results, mode = api.search_dispatch("Act 29", mode="hybrid", limit=5,
                                        storage=st, embed_index=idx)
    assert mode == "hybrid"
    assert results[0]["match_strategy"] == "bm25"


def test_unknown_mode_falls_back_to_hybrid():
    # The safe default is hybrid (BM25 + dense + authority): a bare keyword
    # query must be relevance-ranked, and 'or' (any-word) matches too loosely.
    st = FakeStorage([_bm25_row()])
    results, mode = api.search_dispatch("land", mode="bogus", limit=5,
                                        storage=st)
    assert mode == "hybrid"
    assert results


def test_existing_modes_still_work_with_authority():
    st = FakeStorage([_bm25_row()])
    results, mode = api.search_dispatch("land", mode="and", limit=5,
                                        storage=st)
    assert mode == "and"
    assert results[0]["authority_level"] == "act"
    assert results[0]["status"] == "current"


def _constitution_storage(tmp_path):
    from core.legal.storage import LegalStorage
    s = LegalStorage(str(tmp_path / "const.db"))
    s.connect()
    s.conn.execute(
        "INSERT INTO documents (id,jurisdiction,court,year,citation,title,type,"
        "store_mode,content_available) VALUES (1,'ghana','parliament',1992,"
        "'Constitution 1992 (as amended 1996)','Constitution of the Republic of "
        "Ghana, 1992 (as amended to 1996)','act','full',1)")
    s.conn.execute(
        "INSERT INTO document_versions (document_id,content,content_hash,"
        "version_number) VALUES (1,?,'h',1)",
        ("19. FAIR TRIAL (1) A person shall be given a fair hearing.\n"
         "20. PROTECTION FROM DEPRIVATION OF PROPERTY (1) No property shall be "
         "compulsorily taken possession of.\n"
         "24. ECONOMIC RIGHTS (1) Every person has the right to work under "
         "satisfactory, safe and healthy conditions.\n"
         "243. DISTRICT CHIEF EXECUTIVE (1) There shall be a District Chief "
         "Executive.",))
    s.conn.commit()
    return s


def test_exact_article_query_resolves_constitution_article(tmp_path):
    s = _constitution_storage(tmp_path)
    try:
        results, mode = api.search_dispatch("Article 24", mode="or", limit=5,
                                            storage=s)
        assert results and results[0]["id"] == 1
        assert "24. ECONOMIC RIGHTS" in results[0]["snippet"]
        assert results[0]["match_strategy"] == "article"
    finally:
        s.close()


def test_exact_article_20_returns_deprivation_of_property(tmp_path):
    s = _constitution_storage(tmp_path)
    try:
        results, _ = api.search_dispatch("Article 20", mode="and", limit=5,
                                         storage=s)
        assert results and "PROTECTION FROM DEPRIVATION OF PROPERTY" \
            in results[0]["snippet"]
    finally:
        s.close()
