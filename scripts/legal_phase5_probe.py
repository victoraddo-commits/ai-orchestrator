"""Phase 5 live probe: run_deep over the real CT100 corpus + local GPU model."""
import json
import sys

sys.path.insert(0, "/opt/ai-orchestrator")

from core.juris_kai import reasoning  # noqa: E402


def _clip(text, n=700):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + " …"


def show(query):
    print("=" * 78)
    print("QUERY:", query)
    try:
        res = reasoning.run_deep(query)
    except Exception as exc:  # noqa: BLE001
        print("RUN_DEEP FAILED:", type(exc).__name__, exc)
        return
    print("verdict:", res["verdict"], "| degraded:", res["degraded"])
    print("latency (s):", json.dumps(res["latency"]))
    print("authorities:", res["authorities"])

    adv = res.get("advocate") or {}
    print("\n[ADVOCATE]")
    print("  authorities:", adv.get("authorities"))
    print("  argument:", _clip(adv.get("argument")))

    opp = res.get("opponent") or {}
    print("\n[OPPONENT]")
    print("  authorities:", opp.get("authorities"))
    print("  contrary authorities:", opp.get("contrary_authorities"))
    terms = sorted({s.get("term") for s in (opp.get("contrary_terms") or [])})
    print("  contrary terms found:", terms)
    print("  argument:", _clip(opp.get("argument")))

    j = res.get("judge") or {}
    print("\n[JUDGE]")
    print("  confidence:", j.get("confidence"))
    print("  established:", [r["proposition"][:90] for r in j.get("established", [])])
    print("  disputed:", [r["proposition"][:90] for r in j.get("disputed", [])])
    print("  unresolved:", [r["proposition"][:90] for r in j.get("unresolved", [])])
    u = res.get("uncertainty") or {}
    print("  uncertainty.advocate:", _unc(u.get("advocate")))
    print("  uncertainty.opponent:", _unc(u.get("opponent")))
    irac = j.get("irac") or {}
    for key in ("issue", "rule", "application", "conclusion"):
        print(f"  IRAC.{key}:", _clip(irac.get(key), 400))


def _unc(items):
    from collections import Counter
    return dict(Counter(r.get("status") for r in (items or [])))


if __name__ == "__main__":
    show("rape")
    show("human rights enforcement in ghana")
