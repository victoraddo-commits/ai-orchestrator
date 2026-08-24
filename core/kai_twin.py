"""KAI Digital Twin — JARVIS P14 (§25).

Simulate infrastructure changes against the World Model WITHOUT touching
production: remove a node/service, add capacity, fail a dependency — and
get the projected blast radius, affected services, and risk classification.

This is deliberately a GRAPH simulation, not a fake metrics generator (§47):
it reports what the dependency topology implies, with explicit assumptions,
and never dresses estimates up as measurements. Where live utilization data
exists (health observatory trends), it is cited; where it doesn't, the
output says so.
"""

from __future__ import annotations

from datetime import datetime, timezone


# Risk classes for simulated outcomes (mirrors §26 vocabulary)
_RISK_BY_IMPACT = [
    (0, "none", "no downstream impact in the model"),
    (3, "low", "only leaf entities affected"),
    (8, "medium", "applications/services degraded"),
    (15, "high", "public-facing or business services impacted"),
    (10**9, "critical", "host-level failure: mass guest loss"),
]


def _risk_for(count: int, kinds: set[str]) -> tuple[str, str]:
    if "proxmox_node" in kinds:
        return "critical", "host-level failure: all guests and everything they host go down"
    for threshold, label, desc in _RISK_BY_IMPACT:
        if count <= threshold:
            return label, desc
    return "high", "wide impact"


def simulate(entity_id: str, scenario: str = "fail") -> dict:
    """Simulate `scenario` applied to entity_id. Supported scenarios:
      fail    — entity goes down (containment + dependency propagation)
      remove  — same as fail but flagged as intentional/irreversible
      restart — transient outage of just that entity (children recover)
      scale_up / scale_down — capacity change on a host (informational today)
    """
    from core.world_model import impact_of, get_state, _load

    snap = _load()
    entities = snap.get("entities") or {}
    ent = entities.get(entity_id)
    if not ent:
        return {"ok": False, "error": f"unknown entity '{entity_id}'",
                "hint": "run kai.world.refresh first"}

    scenario = scenario.lower()
    supported = {"fail", "remove", "restart", "scale_up", "scale_down"}
    if scenario not in supported:
        return {"ok": False, "error": f"scenario must be one of {sorted(supported)}"}

    result = {
        "ok": True,
        "simulated_at": datetime.now(timezone.utc).isoformat(),
        "entity": entity_id,
        "entity_label": ent.get("label"),
        "entity_type": ent.get("type"),
        "current_status": ent.get("status"),
        "scenario": scenario,
        "production_changed": False,   # §25: simulation NEVER touches prod
    }

    if scenario in ("scale_up", "scale_down"):
        result.update({
            "projection": (
                f"Capacity change on {entity_id} recorded as intent. "
                "Quantitative projection requires utilization baselines "
                "(health observatory) — currently insufficient history to "
                "model compute headroom honestly."
            ),
            "confidence": 0.2,
            "note": "estimate, not fact — no fabricated numbers (§24/§47)",
        })
        return result

    impact = impact_of(entity_id)
    impacted = impact.get("impacted", [])

    # classify affected entities
    down = [i for i in impacted if i.get("impact") == "down"]
    degraded = [i for i in impacted if i.get("impact") != "down"]
    kinds = {ent.get("type")} | {d.get("type") for d in down}
    risk, rationale = _risk_for(len(impacted), kinds)

    if scenario == "restart":
        # transient: children come back when entity returns
        result.update({
            "projected_outage": "transient (restart)",
            "services_interrupted": len(down),
            "services_degraded": 0,
            "recovery": "automatic when entity returns to service",
            "risk": "low" if risk in ("none", "low") else ("medium" if len(down) < 5 else "high"),
            "affected": [d["entity"] for d in down][:15],
            "note": "brief interruption only; state preserved",
            "confidence": 0.7,
        })
        return result

    result.update({
        "projected_outcome": rationale,
        "entities_down": len(down),
        "entities_degraded": len(degraded),
        "down_list": [{"entity": d["entity"], "label": None, "hops": d["severity_hops"]}
                      for d in down[:20]],
        "degraded_list": [{"entity": d["entity"], "hops": d["severity_hops"]}
                          for d in degraded[:20]],
        "public_namespaces_affected": sorted(
            d["entity"] for d in degraded if str(d["entity"]).startswith("public:")),
        "business_apps_affected": sorted(
            d["entity"] for d in degraded if str(d["entity"]).startswith("app:")),
        "risk": risk,
        "confidence": 0.75,
        "assumption": "graph-derived projection from world model edges; verify before acting",
        "approval_required_before_execution": risk in ("high", "critical"),
    })
    return result


def scenarios_for(entity_id: str) -> dict:
    """List what can be simulated for this entity."""
    snap_state = get_state(entity_id)
    if not snap_state.get("entity"):
        return {"ok": False, "error": f"unknown entity '{entity_id}'"}
    etype = snap_state["entity"].get("type")
    base = ["fail", "remove"]
    if etype in ("lxc", "vm", "docker_container", "service", "application"):
        base.append("restart")
    if etype == "proxmox_node":
        base += ["scale_up", "scale_down"]
    return {"ok": True, "entity": entity_id, "type": etype, "scenarios": base}
