from core.juris_kai import grounding


def _fake_search(monkeypatch, results_by_mode):
    def fake(query, limit=3, mode="or"):
        return results_by_mode.get(mode, [])
    monkeypatch.setattr(grounding, "_search", fake)


def test_grounded_when_phrase_hits(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "Criminal Offences Act", "chunk_content": "x"*500, "citation": "Act 29"}]})
    r = grounding.retrieve("Criminal Offences Act")
    assert r["verdict"] == "GROUNDED" and r["stage"] == 1 and r["docs"]


def test_ungrounded_when_nothing(monkeypatch):
    _fake_search(monkeypatch, {})
    r = grounding.retrieve("quantum entanglement tax")
    assert r["verdict"] == "UNGROUNDED" and r["docs"] == []


def test_partial_when_only_or_hits(monkeypatch):
    _fake_search(monkeypatch, {"or": [{"title": "Some Act", "chunk_content": "y"*500, "citation": "Act 1"}]})
    r = grounding.retrieve("bail application procedure")
    assert r["verdict"] == "PARTIAL" and r["stage"] == 3


def test_empty_query_is_ungrounded_without_searching(monkeypatch):
    calls = []
    monkeypatch.setattr(grounding, "_search",
                        lambda *a, **k: calls.append(a) or [])
    r = grounding.retrieve("   ")
    assert r["verdict"] == "UNGROUNDED" and r["stage"] == 0 and calls == []


def test_search_populates_chunk_content_from_snippet(monkeypatch):
    import core.legal_brain_client as lb
    monkeypatch.setattr(lb, "search", lambda q, limit=3, mode="or": [
        {"id": 1, "title": "T", "snippet": "s" * 500}])
    docs = grounding._search("T")
    assert docs[0]["chunk_content"] == "s" * 500


def test_search_hydrates_short_snippet_from_full_document(monkeypatch):
    import core.legal_brain_client as lb
    monkeypatch.setattr(lb, "search", lambda q, limit=3, mode="or": [
        {"id": 7, "title": "T", "snippet": "short"}])
    monkeypatch.setattr(lb, "get_document", lambda doc_id: {"content": "c" * 900})
    docs = grounding._search("T")
    assert docs[0]["chunk_content"] == "c" * 900


def test_search_keeps_short_snippet_when_document_not_fuller(monkeypatch):
    import core.legal_brain_client as lb
    monkeypatch.setattr(lb, "search", lambda q, limit=3, mode="or": [
        {"id": 9, "title": "T", "snippet": "short"}])
    monkeypatch.setattr(lb, "get_document", lambda doc_id: {"content": "tiny"})
    docs = grounding._search("T")
    assert docs[0]["chunk_content"] == "short"


def test_search_never_crashes_when_document_fetch_fails(monkeypatch):
    import core.legal_brain_client as lb
    monkeypatch.setattr(lb, "search", lambda q, limit=3, mode="or": [
        {"id": 3, "title": "T", "snippet": "short"}])

    def boom(doc_id):
        raise RuntimeError("service down")

    monkeypatch.setattr(lb, "get_document", boom)
    docs = grounding._search("T")
    assert docs[0]["chunk_content"] == "short"


def test_retrieve_grounded_when_full_document_hydrates_short_snippet(monkeypatch):
    import core.legal_brain_client as lb
    monkeypatch.setattr(lb, "search", lambda q, limit=3, mode="or": [
        {"id": 7, "title": "Act", "snippet": "short"}])
    monkeypatch.setattr(lb, "get_document", lambda doc_id: {"content": "c" * 900})
    r = grounding.retrieve("Anything")
    assert r["verdict"] == "GROUNDED" and r["stage"] == 1
