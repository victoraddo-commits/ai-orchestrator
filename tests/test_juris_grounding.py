from core.juris_kai import grounding, legal_context


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


def test_grounded_when_and_hits(monkeypatch):
    _fake_search(monkeypatch, {"and": [{"title": "Some Act", "chunk_content": "y"*500}]})
    r = grounding.retrieve("bail pending appeal")
    assert r["verdict"] == "GROUNDED" and r["stage"] == 2


def test_partial_when_only_like_hits(monkeypatch):
    _fake_search(monkeypatch, {"like": [{"title": "Some Act", "chunk_content": "z"*500}]})
    r = grounding.retrieve("zzx floop")
    assert r["verdict"] == "PARTIAL" and r["stage"] == 4


def test_empty_query_is_ungrounded_without_searching(monkeypatch):
    calls = []
    monkeypatch.setattr(grounding, "_search",
                        lambda *a, **k: calls.append(a) or [])
    r = grounding.retrieve("   ")
    assert r["verdict"] == "UNGROUNDED" and r["stage"] == 0 and calls == []


def test_usable_boundary_399_falls_through_400_usable(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [{"title": "A", "chunk_content": "x"*399}]})
    assert grounding.retrieve("x")["verdict"] == "UNGROUNDED"
    _fake_search(monkeypatch, {"phrase": [{"title": "A", "chunk_content": "x"*400}]})
    assert grounding.retrieve("x")["verdict"] == "GROUNDED"


def test_retrieve_caps_content_at_max_chunk_length(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "T", "chunk_content": "z"*5000, "citation": "Act 1"}]})
    r = grounding.retrieve("T")
    assert r["verdict"] == "GROUNDED"
    assert all(len(d["chunk_content"]) <= legal_context.MAX_CHUNK_LENGTH
               for d in r["docs"])
    assert len(r["docs"][0]["chunk_content"]) == legal_context.MAX_CHUNK_LENGTH


def test_search_is_pure_transport(monkeypatch):
    import core.legal_brain_client as lb
    calls = []

    def fake_search(query, limit=3, mode="or"):
        calls.append((query, limit, mode))
        return [{"id": 1, "title": "T", "snippet": "s"*500}]

    monkeypatch.setattr(lb, "search", fake_search)
    monkeypatch.setattr(lb, "get_document",
                        lambda i: (_ for _ in ()).throw(AssertionError("no hydration")))
    docs = grounding._search("T", 2, mode="phrase")
    assert calls == [("T", 2, "phrase")]
    assert docs[0]["snippet"] == "s" * 500
    assert "chunk_content" not in docs[0]


def test_hydrate_skips_search_only(monkeypatch):
    calls = []
    monkeypatch.setattr("core.legal_brain_client.get_document",
                        lambda i: calls.append(i) or {"content": "c" * 900})
    out = grounding._hydrate({"id": 1, "title": "T", "snippet": "short",
                              "store_mode": "search_only"})
    assert calls == []
    assert out["chunk_content"] == "short"


def test_hydrate_keeps_reference_snippet(monkeypatch):
    calls = []
    monkeypatch.setattr("core.legal_brain_client.get_document",
                        lambda i: calls.append(i) or {"content": "HEAD" + "x" * 2000})
    out = grounding._hydrate({"id": 2, "title": "T",
                              "snippet": "relevance centered snippet",
                              "store_mode": "reference"})
    assert calls == []
    assert out["chunk_content"] == "relevance centered snippet"


def test_hydrate_full_short_snippet_is_fetched_and_capped(monkeypatch):
    calls = []
    monkeypatch.setattr("core.legal_brain_client.get_document",
                        lambda i: calls.append(i) or {"content": "c" * 5000})
    out = grounding._hydrate({"id": 3, "title": "T", "snippet": "short",
                              "store_mode": "full"})
    assert calls == [3]
    assert len(out["chunk_content"]) == legal_context.MAX_CHUNK_LENGTH


def test_hydrate_full_long_snippet_is_not_refetched(monkeypatch):
    calls = []
    monkeypatch.setattr("core.legal_brain_client.get_document",
                        lambda i: calls.append(i) or {"content": "x" * 5000})
    out = grounding._hydrate({"id": 4, "title": "T", "snippet": "s" * 500,
                              "store_mode": "full"})
    assert calls == []
    assert out["chunk_content"] == "s" * 500


def test_hydrate_never_raises_on_fetch_failure(monkeypatch):
    def boom(doc_id):
        raise RuntimeError("service down")

    monkeypatch.setattr("core.legal_brain_client.get_document", boom)
    out = grounding._hydrate({"id": 5, "title": "T", "snippet": "short",
                              "store_mode": "full"})
    assert out["chunk_content"] == "short"


def test_reference_tier_snippet_still_grounds(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "Constitution of Ghana", "store_mode": "reference",
         "chunk_content": "c" * 240, "citation": "1992"}]})
    r = grounding.retrieve("constitution of ghana")
    assert r["verdict"] == "GROUNDED" and r["stage"] == 1 and r["docs"]


def test_reference_tier_stub_is_rejected(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "Constitution of Ghana", "store_mode": "reference",
         "chunk_content": "c" * 50}]})
    r = grounding.retrieve("constitution of ghana")
    assert r["verdict"] == "UNGROUNDED" and r["docs"] == []


def test_search_only_tier_stub_is_rejected(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "Some Index Entry", "store_mode": "search_only",
         "chunk_content": "c" * 50}]})
    r = grounding.retrieve("some index entry")
    assert r["verdict"] == "UNGROUNDED" and r["docs"] == []


def test_full_tier_boundary_399_vs_400(monkeypatch):
    _fake_search(monkeypatch, {"phrase": [
        {"title": "A", "store_mode": "full", "chunk_content": "x" * 399}]})
    assert grounding.retrieve("x")["verdict"] == "UNGROUNDED"
    _fake_search(monkeypatch, {"phrase": [
        {"title": "A", "store_mode": "full", "chunk_content": "x" * 400}]})
    assert grounding.retrieve("x")["verdict"] == "GROUNDED"


def test_footer_full_doc_exact_output():
    docs = [{"title": "Criminal Offences Act", "citation": "Act 29", "year": 1960,
             "court": "Parliament", "store_mode": "full"}]
    assert grounding.build_sources_footer(docs) == (
        "\n\n📚 *Sources*\n1. Criminal Offences Act — Act 29 — 1960 — _full_")


def test_footer_suppresses_citation_equal_to_title():
    docs = [{"title": "Constitution of Ghana", "citation": "Constitution of Ghana",
             "store_mode": "reference"}]
    assert grounding.build_sources_footer(docs) == (
        "\n\n📚 *Sources*\n1. Constitution of Ghana — _reference_")


def test_footer_missing_title_citation_year_mode():
    assert grounding.build_sources_footer([{}]) == "\n\n📚 *Sources*\n1. Untitled"
    assert grounding.build_sources_footer([{"title": "Some Act"}]) == (
        "\n\n📚 *Sources*\n1. Some Act")


def test_footer_is_deterministic():
    docs = [{"title": "A", "citation": "Act 1", "year": 2000, "store_mode": "full"}]
    assert grounding.build_sources_footer(docs) == grounding.build_sources_footer(docs)


def test_footer_empty_when_no_docs():
    assert grounding.build_sources_footer([]) == ""
    assert grounding.build_sources_footer(None) == ""


def test_footer_numbers_and_preserves_order():
    docs = [
        {"title": "First Act", "citation": "Act 1", "store_mode": "full"},
        {"title": "Second Act", "citation": "Act 2", "store_mode": "reference"},
    ]
    assert grounding.build_sources_footer(docs) == (
        "\n\n📚 *Sources*\n"
        "1. First Act — Act 1 — _full_\n"
        "2. Second Act — Act 2 — _reference_")


def test_grounded_prompt_forbids_outside_citations():
    from core.juris_kai.prompt import build_grounded_prompt
    p = build_grounded_prompt(
        "legal_research", "theft penalty", verdict="GROUNDED",
        docs=[{"title": "Act 29", "chunk_content": "Stealing..."}])
    assert "only" in p.lower() and "Act 29" in p


def test_ungrounded_prompt_says_do_not_answer():
    from core.juris_kai.prompt import build_grounded_prompt
    p = build_grounded_prompt("legal_research", "x", verdict="UNGROUNDED", docs=[])
    assert "do not" in p.lower()


def test_partial_prompt_marks_unverified_points():
    from core.juris_kai.prompt import build_grounded_prompt
    p = build_grounded_prompt(
        "legal_research", "bail", verdict="PARTIAL",
        docs=[{"title": "Act 1", "chunk_content": "Bail is..."}])
    assert "unverified" in p.lower() and "Act 1" in p


def test_grounded_prompt_caps_each_source_quote():
    from core.juris_kai.prompt import build_grounded_prompt
    p = build_grounded_prompt(
        "legal_research", "x", verdict="GROUNDED",
        docs=[{"title": "T", "chunk_content": "z" * 5000}])
    assert "z" * legal_context.MAX_CHUNK_LENGTH in p
    assert "z" * (legal_context.MAX_CHUNK_LENGTH + 1) not in p
