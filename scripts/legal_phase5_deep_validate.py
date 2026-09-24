#!/usr/bin/env python3
"""Phase 5 Task 4 — live Quick (single-pass) vs Deep (3-pass) validation.

Runs the same real questions against the CT100 legal corpus and the local
VM104 GPU model (``qwen3-coder:kai``) through the production seams:

  * Quick = ``grounding.build_grounded_plan`` + ``streaming.generate`` (one pass)
  * Deep  = ``reasoning.run_deep`` over the *same* retrieved docs — the exact
            wiring the bot uses, so the measured latency is the real cost.

Local-only. Writes ``reports/legal_phase5_deep_validate_<date>.{json,md}``.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/ai-orchestrator")

from core.juris_kai import grounding, reasoning, streaming  # noqa: E402

REPO = Path("/opt/ai-orchestrator")
DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

QUESTIONS = [
    {"key": "rape", "area": "criminal",
     "q": "What is the offence of rape under Ghanaian law?"},
    {"key": "human_rights", "area": "human_rights",
     "q": "How are human rights enforced in Ghana?"},
    {"key": "civil_procedure", "area": "procedure",
     "q": "What are the civil procedure rules for summary judgment in Ghana?"},
    {"key": "bill", "area": "bill",
     "q": "What does the Promotion of Proper Human Sexual Rights and Ghanaian "
          "Family Values Bill 2021 provide?"},
    # Contentless (no discriminating token): the strict gate must refuse it.
    {"key": "absurd_vacuous", "area": "absurd",
     "q": "ghana law"},
    # Absurd but token-bearing: documents the known retrieval over-permissiveness
    # (the OR/LIKE fallback still matches unrelated OCR text). Pre-existing gap.
    {"key": "absurd_nonsense", "area": "absurd",
     "q": "What is the Ghana tax treatment of quantum entanglement?"},
]

_CONTRARY_HINTS = (
    "except", "notwithstanding", "shall not apply", "does not apply",
    "provided that", "repeal", "amendment", "amended", "proviso",
)


def _clip(text, n=260):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _tags(text):
    return sorted({w.strip("[]") for w in str(text or "").split()
                   if w.startswith("[") and w.endswith("]")})


def _mentions_contrary(text):
    low = str(text or "").lower()
    return sorted({h for h in _CONTRARY_HINTS if h in low})


def _firewall(text):
    """The production answer transform (strip unverifiable citations)."""
    from core.juris_kai import citation_firewall
    return citation_firewall.apply_citation_firewall(text)["text"]


def verify_citations(text):
    """Re-audit the delivered text against the CT100 citation verifier."""
    try:
        from core import legal_brain_client as lb
        report = lb.verify_citations(text, record=False)
        cites = report.get("citations") or []
        bad = [c for c in cites
               if (c.get("status") or "").upper() != "VERIFIED"]
        return {"checked": len(cites), "unverified": len(bad),
                "all_verified": not bad,
                "unverified_displays": [str(c.get("display") or "")
                                        for c in bad][:12]}
    except Exception as exc:  # noqa: BLE001 - report, never crash validation
        return {"checked": 0, "unverified": None,
                "error": f"{type(exc).__name__}: {exc}"}


def run_question(item):
    q = item["q"]
    row = {"key": item["key"], "area": item["area"], "question": q}

    t0 = time.perf_counter()
    plan = grounding.build_grounded_plan(q, "juris_research")
    row["retrieve_s"] = round(time.perf_counter() - t0, 2)
    row["verdict"] = plan["verdict"]
    row["authority_count"] = len(plan["docs"])
    row["sources"] = [str((d.get("title") or "").strip())
                      for d in plan["docs"]]
    row["footer_status_tags"] = _tags(plan["footer"])

    if plan["refusal"]:
        row["quick"] = {"refused": True, "latency_s": 0.0,
                        "answer": plan["refusal"]}
        row["deep"] = {"refused": True, "latency_s": 0.0,
                       "established": 0, "disputed": 0, "unresolved": 0,
                       "authority_count": 0, "contrary_authorities": [],
                       "confidence": None, "degraded": False,
                       "answer": plan["refusal"]}
        return row

    # Quick — one grounded pass (the bot's default for ordinary messages).
    t0 = time.perf_counter()
    quick_ans = streaming.generate(plan["prompt"], task_type="juris_research")
    quick_s = round(time.perf_counter() - t0, 2)
    # Mirror production: the bot firewalls the model answer before delivery.
    quick_clean = _firewall(quick_ans or "")
    quick_text = plan["banner"] + quick_clean + plan["footer"]
    row["quick"] = {
        "refused": False, "latency_s": quick_s,
        "answer": _clip(quick_ans),
        "mentions_contrary": _mentions_contrary(quick_ans),
        "firewall": verify_citations(quick_text),
    }

    # Deep — three passes over the SAME docs the bot would pass in.
    t0 = time.perf_counter()
    res = reasoning.run_deep(q, docs=plan["docs"])
    deep_s = round(time.perf_counter() - t0, 2)
    # Mirror the bot: firewall the model narrative, lists/footer stay corpus-fed.
    body = reasoning.render_deep(res, narrative_transform=_firewall)
    deep_text = plan["banner"] + body + plan["footer"]
    opp = res.get("opponent") or {}
    judge = res.get("judge") or {}
    row["deep"] = {
        "refused": False,
        "latency_s": deep_s,
        "latency_parts": res.get("latency"),
        "degraded": res.get("degraded"),
        "authority_count": len(res.get("authorities") or []),
        "established": len(judge.get("established") or []),
        "disputed": len(judge.get("disputed") or []),
        "unresolved": len(judge.get("unresolved") or []),
        "confidence": judge.get("confidence"),
        "contrary_authorities": list(opp.get("contrary_authorities") or []),
        "contrary_terms": sorted(
            {s.get("term") for s in (opp.get("contrary_terms") or [])}),
        "irac": {k: _clip(v, 300)
                 for k, v in (judge.get("irac") or {}).items()},
        "answer": _clip(body, 400),
        "firewall": verify_citations(deep_text),
    }
    return row


def render_md(rows, generated):
    lines = [
        "# Legal Brain 2.0 — Phase 5 Task 4: Quick vs Deep live validation",
        "",
        f"Generated: {generated}",
        "",
        "Model: local VM104 `qwen3-coder:kai`  ·  Corpus: CT100 (live)",
        "Quick = single grounded pass  ·  Deep = retrieve → advocate → oppose → judge",
        "",
        "| Question | Quick verdict | Quick lat (s) | Deep est/disp/unres | Deep auths "
        "| Deep contrary | Deep lat (s) | Degraded |",
        "|----------|---------------|--------------:|:-------------------:|"
        "-----------:|--------------:|-------------:|:--------:|",
    ]
    for r in rows:
        q = r["key"]
        qv = r["verdict"]
        ql = r["quick"]["latency_s"]
        d = r["deep"]
        if d.get("refused"):
            shape = "refused"
            dl = d["latency_s"]
            auths = contr = 0
            deg = "-"
        else:
            shape = f"{d['established']}/{d['disputed']}/{d['unresolved']}"
            dl = d["latency_s"]
            auths = d["authority_count"]
            contr = len(d["contrary_authorities"])
            deg = "yes" if d["degraded"] else "no"
        lines.append(
            f"| {q} | {qv} | {ql} | {shape} | {auths} | {contr} | {dl} | {deg} |")

    lines += ["", "## Per-question detail", ""]
    for r in rows:
        lines.append(f"### {r['key']} — {r['question']}")
        lines.append(f"- verdict: **{r['verdict']}** · retrieved sources: "
                     f"{r['authority_count']} · footer tags: "
                     f"{r['footer_status_tags'] or '—'}")
        lines.append(f"- Quick ({r['quick']['latency_s']}s): "
                     f"{r['quick'].get('answer', '')}")
        if r["quick"].get("mentions_contrary"):
            lines.append(f"  - quick contrary hints: "
                         f"{r['quick']['mentions_contrary']}")
        d = r["deep"]
        if d.get("refused"):
            lines.append(f"- Deep: refused (no passes; {d['latency_s']}s): "
                         f"{d.get('answer', '')}")
        else:
            parts = d.get("latency_parts") or {}
            lines.append(
                f"- Deep ({d['latency_s']}s; retrieve {parts.get('retrieve')} · "
                f"advocate {parts.get('advocate')} · opponent "
                f"{parts.get('opponent')} · judge {parts.get('judge')}): "
                f"est/disp/unres = {d['established']}/{d['disputed']}/"
                f"{d['unresolved']} · confidence {d['confidence']} · "
                f"degraded {d['degraded']}")
            lines.append(f"  - authorities: {d['authority_count']} · "
                         f"counter-authorities: "
                         f"{d['contrary_authorities'] or 'none'} · "
                         f"contrary terms: {d['contrary_terms'] or 'none'}")
            lines.append(f"  - IRAC issue: {d['irac'].get('issue', '')}")
            lines.append(f"  - IRAC rule: {d['irac'].get('rule', '')}")
            lines.append(f"  - firewall audit quick: {r['quick'].get('firewall')}")
            lines.append(f"  - firewall audit deep:  {d['firewall']}")
            for label, fw in (("quick", r["quick"].get("firewall")),
                              ("deep", d.get("firewall"))):
                if fw and fw.get("unverified_displays"):
                    lines.append(f"    - {label} residual verifier flags "
                                 f"(corpus identity strings): "
                                 f"{fw['unverified_displays']}")
        lines.append("")

    # Checks
    bill = next((r for r in rows if r["key"] == "bill"), None)
    absurd_rows = [r for r in rows if r["key"].startswith("absurd")]
    contrary_rows = [r["key"] for r in rows
                     if not r["deep"].get("refused")
                     and r["deep"].get("contrary_authorities")]
    lines += ["## Checks", ""]
    if bill:
        proposed = "PROPOSED" in bill["footer_status_tags"]
        lines.append(f"- Bill question verdict **{bill['verdict']}**, footer tags "
                     f"{bill['footer_status_tags']} — "
                     f"{'PROPOSED bill labelled, not presented as enacted law' if proposed else 'bill status tag NOT surfaced'}.")
    for r in absurd_rows:
        refused = r["verdict"] in ("UNGROUNDED", "OUT_OF_SCOPE")
        lines.append(
            f"- Absurd `{r['key']}` (\"{r['question']}\") verdict "
            f"**{r['verdict']}** — "
            + ("refused, no passes/model call." if refused else
               "ATTENTION: not refused (pre-existing retrieval fallback gap)."))
    lines.append(f"- Deep found counter-authorities for: "
                 f"{contrary_rows or 'none'}")
    return "\n".join(lines)


def main():
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for item in QUESTIONS:
        print(f"[validate] {item['key']}: {item['q']}", flush=True)
        try:
            row = run_question(item)
        except Exception as exc:  # noqa: BLE001 - keep validating the rest
            row = {"key": item["key"], "area": item["area"],
                   "question": item["q"], "error": f"{type(exc).__name__}: {exc}",
                   "verdict": "ERROR", "quick": {"refused": True,
                                                 "latency_s": 0.0},
                   "deep": {"refused": True, "latency_s": 0.0}}
        rows.append(row)
        print(f"  verdict={row.get('verdict')} "
              f"quick={row.get('quick', {}).get('latency_s')}s "
              f"deep={row.get('deep', {}).get('latency_s')}s", flush=True)

    report = {"generated": generated, "model": streaming.DEFAULT_MODEL,
              "corpus": "CT100", "rows": rows}
    out_dir = REPO / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"legal_phase5_deep_validate_{DATE}.json").write_text(
        json.dumps(report, indent=2))
    (out_dir / f"legal_phase5_deep_validate_{DATE}.md").write_text(
        render_md(rows, generated))
    print(f"wrote reports/legal_phase5_deep_validate_{DATE}.{{json,md}}")


if __name__ == "__main__":
    main()
