"""TDD regression: the SOURCES block must respect a total-char budget.

30 sources of 2000 chars each previously produced a judge prompt > 47k bytes —
beyond the 8k-token context — so ollama silently truncated the very sources the
judge must ground on. The block is now capped at MAX_SOURCES_TOTAL_CHARS and
must still include at least the leading sources.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import reasoning  # noqa: E402
from core.juris_kai import prompts_reasoning  # noqa: E402
from core.juris_kai.prompts_reasoning import (  # noqa: E402
    MAX_SOURCES_TOTAL_CHARS,
    _sources_block,
    build_judge_prompt,
)


def _doc(i: int) -> dict:
    return {
        "id": i,
        "title": f"Act {i}",
        "citation": f"Act {i}",
        "authority_level": "act",
        "chunk_content": ("Act 1 provision text. " * 4
                          + "x" * (2000 - len("Act 1 provision text. " * 4))),
    }


def test_judge_prompt_with_30_docs_is_capped_under_25k():
    prompt = build_judge_prompt(
        "Is rape an offence?", "advocate argument.", "opponent argument.",
        [_doc(i) for i in range(1, 31)])
    assert len(prompt.encode("utf-8")) < 25000


def test_judge_prompt_keeps_leading_sources():
    prompt = build_judge_prompt(
        "Is rape an offence?", "advocate argument.", "opponent argument.",
        [_doc(i) for i in range(1, 31)])
    assert "Act 1" in prompt


def test_sources_block_respects_total_budget():
    block = _sources_block([_doc(i) for i in range(1, 31)])
    assert len(block) <= MAX_SOURCES_TOTAL_CHARS + 100  # separators slack


def test_sources_block_small_input_unchanged():
    docs = [_doc(1), _doc(2)]
    block = _sources_block(docs)
    assert "SOURCE 1: Act 1" in block
    assert "SOURCE 2: Act 2" in block


def test_sources_block_empty_and_none_safe():
    assert _sources_block([]) == ""
    assert _sources_block(None) == ""


def test_contrary_union_capped_to_five_by_bm25_rank(monkeypatch):
    hits = [{"id": 100 + i, "title": f"Act C{i}", "citation": f"Act C{i}",
             "chunk_content": f"This Act shall not apply to {i} cases.",
             "bm25_rank": i} for i in range(1, 11)]
    seen = []

    def fake_search(query, limit=4, mode="hybrid"):
        seen.append(query)
        return hits

    monkeypatch.setattr(reasoning, "_search", fake_search)
    out = reasoning._retrieve_contrary("Is rape an offence?", [])
    assert len(out) == 5
    assert [d["bm25_rank"] for d in out] == [1, 2, 3, 4, 5]
    assert len(seen) > 0  # span-scanning terms were still searched


def test_contrary_union_deduped_before_cap(monkeypatch):
    hit = {"id": 100, "title": "Act C", "citation": "Act C",
           "chunk_content": "This Act shall not apply.",
           "bm25_rank": 1}

    def fake_search(query, limit=4, mode="hybrid"):
        return [hit, dict(hit)]

    monkeypatch.setattr(reasoning, "_search", fake_search)
    out = reasoning._retrieve_contrary("Is rape an offence?", [])
    assert len(out) == 1


def test_oppose_pass_still_scans_contrary_spans(monkeypatch):
    monkeypatch.setattr(reasoning, "_search", lambda q, limit=4, mode="hybrid": [])
    monkeypatch.setattr(reasoning, "_generate", lambda prompt, tt: "No contrary authority in the supplied sources.")
    monkeypatch.setattr(reasoning, "_verify",
                        lambda text: {"citations": [], "summary": {}, "all_verified": True})
    doc = _doc(1)
    doc["chunk_content"] = ("Any person may apply; provided that the tribunal "
                            "shall not apply this section to minors.")
    out = reasoning.oppose("Is rape an offence?", [doc], "The advocate says X.")
    assert out["contrary_terms"], "span scanning must survive the cap change"
