"""Tests for LegalStorage.search(): normalized FTS query, content snippet,
and store_mode passthrough.

Regression: raw punctuation in a query raised 'fts5: syntax error', and the
snippet used FTS column 2 (court) instead of column 0 (content).
"""
import pytest

from core.legal.storage import LegalStorage


@pytest.fixture
def st(tmp_path):
    s = LegalStorage(str(tmp_path / "t.db"))
    s.connect()
    s.conn.execute(
        "INSERT INTO documents "
        "(id,jurisdiction,court,year,citation,title,type,store_mode)"
        " VALUES (1,'ghana','Supreme Court',1960,'Act 29',"
        "'Criminal Offences Act','act','full')")
    s.conn.execute(
        "INSERT INTO fts_documents "
        "(rowid,content,jurisdiction,court,year,citation,judge,parties,title)"
        " VALUES (1,'Stealing is a criminal offence punishable by imprisonment.',"
        "'ghana','Supreme Court',1960,'Act 29','','','Criminal Offences Act')")
    s.conn.commit()
    yield s
    s.close()


def test_punctuation_query_does_not_raise(st):
    rows = st.search("Criminal Offences Act, 1960 (Act 29)?")
    assert rows, "expected a hit, got none"


def test_snippet_uses_content_not_court(st):
    rows = st.search("stealing")
    assert "imprisonment" in rows[0]["snippet"]
    # snippet is plain text; no highlight markers are ever introduced
    assert "<mark>" not in rows[0]["snippet"]


def test_search_returns_store_mode(st):
    assert st.search("stealing")[0]["store_mode"] == "full"


def test_empty_query_returns_empty(st):
    assert st.search("???") == []


def test_and_mode_requires_all_terms(st):
    assert st.search("stealing imprisonment", mode="and")
    assert st.search("stealing unicorn", mode="and") == []


def test_invalid_mode_raises(st):
    with pytest.raises(ValueError):
        st.search("stealing", mode="bogus")


@pytest.fixture
def st_like(tmp_path):
    """Doc whose title keyword differs from its FTS tokens by inflection."""
    s = LegalStorage(str(tmp_path / "t2.db"))
    s.connect()
    s.conn.execute(
        "INSERT INTO documents "
        "(id,jurisdiction,court,year,citation,title,type,store_mode)"
        " VALUES (1,'ghana','High Court',1992,'Act 1',"
        "'Criminal Offences Act','act','full')")
    # document_versions drives the FTS sync trigger and the LIKE fallback.
    s.conn.execute(
        "INSERT INTO document_versions "
        "(document_id,content,content_hash,version_number)"
        " VALUES (1,'Stealing is a criminal act punishable by imprisonment.',"
        "'x',1)")
    s.conn.commit()
    yield s
    s.close()


def test_like_mode_finds_title_keyword_fts_misses(st_like):
    # FTS5 matches whole tokens, so singular "offence" != the title token
    # "offences" and the FTS path returns nothing.
    assert st_like.search("offence") == []
    rows = st_like.search("offence", mode="like")
    assert rows, "LIKE fallback should match the title substring"
    assert rows[0]["title"] == "Criminal Offences Act"
    assert "snippet" in rows[0]
    assert rows[0]["store_mode"] == "full"


def test_like_mode_empty_query_returns_empty(st_like):
    assert st_like.search("???", mode="like") == []


@pytest.fixture
def st_like_mid(tmp_path):
    """Doc whose keyword sits deep in the content, past the head window."""
    s = LegalStorage(str(tmp_path / "t3.db"))
    s.connect()
    s.conn.execute(
        "INSERT INTO documents "
        "(id,jurisdiction,court,year,citation,title,type,store_mode)"
        " VALUES (1,'ghana','High Court',1992,'Act 2',"
        "'State Lands Act','act','full')")
    content = ("preliminary recitals and procedural background. " * 30
               + "The board may grant a right of occupancy to any applicant.")
    s.conn.execute(
        "INSERT INTO document_versions "
        "(document_id,content,content_hash,version_number) VALUES (1,?,'x',1)",
        (content,))
    s.conn.commit()
    yield s
    s.close()


def test_like_snippet_is_match_centred(st_like_mid):
    rows = st_like_mid.search("occupancy", mode="like")
    assert rows, "expected a LIKE hit on content"
    snippet = rows[0]["snippet"]
    assert "occupancy" in snippet.lower()
    assert not snippet.lower().startswith("preliminary"), \
        "snippet must be centred on the hit, not the head of the content"


@pytest.fixture
def st_numeric(tmp_path):
    """Three docs: a bare "24", a "243" and a "2024"."""
    s = LegalStorage(str(tmp_path / "tn.db"))
    s.connect()
    for doc_id, (title, content) in enumerate([
        ("Constitution 1992", "24. ECONOMIC RIGHTS (1) Every person has the "
                              "right to work."),
        ("District Assemblies Act", "See article 243 of the Constitution."),
        ("Budget Act", "This Act applies to the 2024 financial year."),
    ], start=1):
        s.conn.execute(
            "INSERT INTO documents "
            "(id,jurisdiction,court,year,citation,title,type,store_mode)"
            " VALUES (?, 'ghana','High Court',1992,?,?, 'act','full')",
            (doc_id, f"Doc {doc_id}", title))
        s.conn.execute(
            "INSERT INTO document_versions "
            "(document_id,content,content_hash,version_number)"
            " VALUES (?,?,'x',1)", (doc_id, content))
    s.conn.commit()
    yield s
    s.close()


def test_like_numeric_token_has_word_boundary(st_numeric):
    ids = {r["id"] for r in st_numeric.search("24", mode="like")}
    assert 1 in ids, "a bare '24' token must match"
    assert 2 not in ids, "'24' must not match '243'"
    assert 3 not in ids, "'24' must not match '2024'"


# ── snippet-free fetch + snippet_batch (fix C, 2026-09-26) ────────────────

def test_search_with_snippet_false_skips_snippet_column(st):
    rows = st.search("criminal offence", limit=5, mode="or",
                     with_snippet=False)
    assert rows, "snippet-free search must still match"
    assert all("snippet" not in r for r in rows), (
        "with_snippet=False must not compute FTS snippets")
    assert rows[0]["id"] == 1
    assert rows[0]["title"] == "Criminal Offences Act"
    assert rows[0]["bm25_rank"] == 1


def test_snippet_batch_returns_snippets_for_shortlist(st):
    out = st.snippet_batch("criminal offence", [1], mode="or")
    assert 1 in out
    assert "criminal" in (out[1] or "").lower()


def test_snippet_batch_empty_inputs(st):
    assert st.snippet_batch("", [1]) == {}
    assert st.snippet_batch("criminal", []) == {}
