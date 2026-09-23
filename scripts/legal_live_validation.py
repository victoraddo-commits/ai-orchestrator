#!/usr/bin/env python3
"""End-to-end live validation of the Juris Kai grounding path (Task 9).

For a set of topics — including the previously-failing ones — this runs the
**real** grounding gate (``core.juris_kai.grounding.build_grounded_plan``, the
same gate every legal-answer surface uses) and, for a bounded subset, the real
answer path (``core.juris_kai.bot._build_legal_reply``, which may call the
model). It then verifies that every cited source resolves to a real corpus
document and emits a JSON + Markdown report with an honest gap list.

    python3 scripts/legal_live_validation.py
    python3 scripts/legal_live_validation.py --answer-topics rape --out-dir ...

Isolation: a temporary ``JURIS_KAI_DB_DIR`` is used so the validator never
touches the production account DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Point the bot at an isolated account store BEFORE importing it, so validation
# never reads or writes the production account DB. The operator can override the
# location with JURIS_KAI_VALIDATE_DB_DIR.
os.environ["JURIS_KAI_DB_DIR"] = os.environ.get(
    "JURIS_KAI_VALIDATE_DB_DIR",
    os.path.join(tempfile.gettempdir(), "juris_kai_validate"))

from core import legal_brain_client as lb  # noqa: E402
from core.juris_kai import grounding  # noqa: E402

# topic -> (area, expectation). Expectation is one of:
#   grounded | partial | ungrounded | refused
TOPICS = [
    ("rape", "criminal", "grounded"),
    ("offence of rape", "criminal", "grounded"),
    ("human rights", "human_rights", "any"),
    ("human rights enforcement in ghana", "human_rights", "any"),
    ("burden of proof", "evidence", "any"),
    ("admissibility of confession evidence", "evidence", "any"),
    ("divorce", "family", "any"),
    ("custody of children on divorce", "family", "any"),
    ("summary judgment", "procedure", "any"),
    ("civil procedure rules", "procedure", "any"),
    ("ghana xylophone zzz", "absurd", "ungrounded"),
    ("law of kenya on rape", "foreign", "refused"),
]

# Topics whose *real model answer* we additionally generate (bounded).
DEFAULT_ANSWER_TOPICS = ("rape",)

# Lightweight title hints used to judge whether a retrieved source is actually
# on-topic for the expected legal area (retrieval-quality check, not a hard
# gate). Kept here so the validator does not need the CT 100 corpus modules.
AREA_HINTS = {
    "criminal": ("criminal", "offence", "offense", "penal"),
    "human_rights": ("human rights", "chraj", "fundamental rights",
                     "civil liberties", "commission on human rights"),
    "evidence": ("evidence", "witness", "confession", "affidavit", "proof"),
    "family": ("marriage", "matrimonial", "divorce", "custody", "children",
               "succession", "intestate", "maintenance", "family"),
    "procedure": ("procedure", "court rules", "high court", "practice direction",
                  "rules of court", "judgment"),
}


def topical_match(area: str, sources: list):
    """True/False/None: does any source title look on-topic for ``area``?"""
    hints = AREA_HINTS.get(area)
    if not hints or not sources:
        return None
    for s in sources:
        title = (s.get("title") or "").lower()
        if any(h in title for h in hints):
            return True
    return False


def _source_view(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "title": (doc.get("title") or "").strip(),
        "citation": (doc.get("citation") or "").strip(),
        "year": doc.get("year"),
        "store_mode": doc.get("store_mode"),
    }


def citation_is_real(doc: dict) -> dict:
    """Verify a retrieved source resolves to a stored corpus document."""
    doc_id = doc.get("id")
    if doc_id is None:
        return {"real": False, "reason": "no id"}
    try:
        row = lb.get_document(doc_id) or {}
    except Exception as exc:  # noqa: BLE001
        return {"real": False, "reason": f"fetch failed: {type(exc).__name__}"}
    if not row or row.get("error"):
        return {"real": False, "reason": "not found in corpus"}
    got = (row.get("citation") or "").strip()
    want = (doc.get("citation") or "").strip()
    if want and got and want.lower() != got.lower():
        return {"real": True, "reason": "id resolves; citation string differs",
                "stored_citation": got}
    return {"real": True, "reason": "id resolves to stored document"}


def validate_topic(query: str, area: str, expect: str) -> dict:
    """Run the real grounding gate for one topic (no model)."""
    entry = {"query": query, "area": area, "expect": expect}
    try:
        plan = grounding.build_grounded_plan(query)
    except Exception as exc:  # noqa: BLE001
        entry.update({"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}",
                      "groundable": False})
        return entry
    docs = plan.get("docs") or []
    checks = [citation_is_real(d) for d in docs]
    sources = [_source_view(d) for d in docs]
    entry.update({
        "verdict": plan.get("verdict"),
        "out_of_scope": plan.get("out_of_scope"),
        "groundable": plan.get("groundable"),
        "refusal": plan.get("refusal"),
        "sources": sources,
        "topical": topical_match(area, sources),
        "citations_real": all(c["real"] for c in checks) if checks else False,
        "citation_checks": checks,
    })
    entry["meets_expectation"] = _meets(expect, entry)
    return entry


def _meets(expect: str, entry: dict) -> bool:
    verdict = entry.get("verdict")
    if expect == "grounded":
        return verdict == "GROUNDED"
    if expect == "ungrounded":
        return verdict == "UNGROUNDED"
    if expect == "refused":
        return bool(entry.get("out_of_scope")) or verdict == "OUT_OF_SCOPE"
    # "any" = must not crash and must ground (weak-topic goal)
    return verdict in ("GROUNDED", "PARTIAL")


def _make_account():
    from core.juris_kai.accounts import get_account_manager
    mgr = get_account_manager()
    tid = "9" + str(uuid.uuid4().int)[:9]
    acct = mgr.get_or_create(tid, "Validator")
    mgr.accept_disclaimer(acct["account_id"])
    return acct


def validate_answer(query: str) -> dict:
    """Run the real bot answer path (may call the model) for one topic."""
    import core.juris_kai.bot as bot
    acct = _make_account()
    chat_id = "val-" + str(uuid.uuid4().int)[:8]
    t0 = time.time()
    try:
        resp = bot._build_legal_reply(query, chat_id, acct)
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "error": f"{type(exc).__name__}: {exc}"}
    text = (resp.get("text") or "").strip()
    refusal = text in (grounding.UNGROUNDED_REPLY, grounding.JURISDICTION_REFUSAL)
    cites = "📚" in text or "Sources" in text
    return {
        "query": query,
        "answer_chars": len(text),
        "is_refusal": refusal,
        "cites_sources": cites,
        "has_partial_banner": text.startswith(grounding.PARTIAL_BANNER.strip()[:8]),
        "latency_s": round(time.time() - t0, 1),
        "answer_preview": text[:1200],
    }


def build_report(answer_topics=DEFAULT_ANSWER_TOPICS) -> dict:
    topics = [validate_topic(q, a, e) for q, a, e in TOPICS]
    answers = [validate_answer(q) for q in answer_topics]
    gaps = _gaps(topics, answers)
    passed = sum(1 for t in topics if t.get("meets_expectation"))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "legal_brain": {"documents": _doc_count()},
        "topics_passed": passed,
        "topics_total": len(topics),
        "topics": topics,
        "answers": answers,
        "gaps": gaps,
    }


def _doc_count():
    try:
        return (lb.health() or {}).get("documents")
    except Exception:  # noqa: BLE001
        return None


def _gaps(topics, answers) -> list:
    gaps = []
    for t in topics:
        verdict = t.get("verdict")
        if verdict == "ERROR":
            gaps.append({"query": t["query"], "area": t["area"],
                         "gap": "retrieval error",
                         "path": "check legal brain service / network"})
            continue
        if (t.get("expect") in ("grounded", "ungrounded", "refused")
                and not t.get("meets_expectation")):
            gaps.append({
                "query": t["query"], "area": t["area"],
                "gap": f"expected {t['expect']}, got {verdict}",
                "path": _acquisition_path(t)})
            continue
        if t.get("sources") and not t.get("citations_real"):
            gaps.append({
                "query": t["query"], "area": t["area"],
                "gap": "grounded but a cited source did not resolve",
                "path": "verify /integrity and re-harvest the cited record"})
            continue
        if t.get("topical") is False:
            gaps.append({
                "query": t["query"], "area": t["area"],
                "gap": (f"grounded, but no retrieved source is on-topic for "
                        f"{t['area']} — retrieval fell back to adjacent areas "
                        "(missing dedicated source)"),
                "path": _acquisition_path(t)})
    for a in answers:
        if a.get("error"):
            gaps.append({"query": a["query"], "area": "answer",
                         "gap": f"model path error: {a['error']}",
                         "path": "check model fabric / ai_router"})
        elif a.get("is_refusal"):
            gaps.append({"query": a["query"], "area": "answer",
                         "gap": "grounded retrieval but the bot refused (no model answer)",
                         "path": "investigate generation path"})
    return gaps


def _acquisition_path(topic) -> str:
    area = topic.get("area")
    table = {
        "human_rights": ("harvest/enable a GREEN human-rights source "
                         "(e.g. CHRAJ reports, ghalii legislation) and re-run "
                         "the weekly cycle; weak-area stubs are prioritised"),
        "evidence": ("harvest the Evidence Act and evidence-related judgments; "
                     "weak-area stubs are prioritised by the weekly cycle"),
        "family": ("harvest matrimonial/custody statutes and judgments; "
                   "weak-area stubs are prioritised by the weekly cycle"),
        "procedure": ("harvest High Court (Civil Procedure) Rules and "
                      "practice directions; weak-area stubs are prioritised"),
    }
    return table.get(area, "acquire a GREEN source covering this topic, then "
                           "re-run the weekly harvest cycle")


def render_markdown(report: dict) -> str:
    lines = [
        "# Legal Brain 2.0 — live grounding validation (Task 9)",
        "",
        f"Generated: {report['generated_at']}",
        f"Corpus documents: {report.get('legal_brain', {}).get('documents')}",
        f"Topics meeting expectation: {report['topics_passed']}/"
        f"{report['topics_total']}",
        "",
        "| Topic | Area | Verdict | Grounded | Cites real source | On-topic | Meets |",
        "|-------|------|---------|----------|-------------------|----------|-------|",
    ]
    for t in report["topics"]:
        src = t.get("sources") or []
        cite = "yes" if t.get("citations_real") else ("n/a" if not src else "NO")
        topical = t.get("topical")
        top = "n/a" if topical is None else ("yes" if topical else "NO")
        lines.append(
            f"| {t['query']} | {t['area']} | {t.get('verdict')} | "
            f"{'yes' if t.get('groundable') else 'no'} | {cite} | {top} | "
            f"{'PASS' if t.get('meets_expectation') else 'GAP'} |")
    lines += ["", "## Sources per grounded topic", ""]
    for t in report["topics"]:
        if t.get("sources"):
            lines.append(f"* **{t['query']}** — {t.get('verdict')}")
            for s in t["sources"]:
                lines.append(f"  * {s['title'] or '(untitled)'} — "
                             f"{s['citation'] or '-'} — _{s['store_mode']}_")
    lines += ["", "## Model answers (bounded subset)", ""]
    for a in report["answers"]:
        lines.append(f"* **{a['query']}** — chars={a.get('answer_chars')} "
                     f"refusal={a.get('is_refusal')} "
                     f"cites={a.get('cites_sources')} "
                     f"({a.get('latency_s')}s)")
    lines += ["", "## Honest gap list", ""]
    if not report["gaps"]:
        lines.append("(none)")
    for g in report["gaps"]:
        lines.append(f"* `{g['query']}` ({g['area']}): {g['gap']}")
        lines.append(f"  * acquisition path: {g['path']}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--answer-topics", default=",".join(DEFAULT_ANSWER_TOPICS),
                    help="comma-separated topics to answer with the real model")
    ap.add_argument("--skip-answers", action="store_true")
    args = ap.parse_args()

    answer_topics = () if args.skip_answers else tuple(
        t.strip() for t in args.answer_topics.split(",") if t.strip())
    report = build_report(answer_topics=answer_topics)

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = report["generated_at"][:10]
    json_path = os.path.join(args.out_dir, f"legal_live_validation_{stamp}.json")
    md_path = os.path.join(args.out_dir, f"legal_live_validation_{stamp}.md")
    with open(json_path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(md_path, "w") as fh:
        fh.write(render_markdown(report))
    print(render_markdown(report))
    print(f"\nwritten: {json_path}\nwritten: {md_path}")


if __name__ == "__main__":
    main()
