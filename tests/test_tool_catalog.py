"""§12 — tools declare risk, permissions, environments, auth, rollback, audit."""
from __future__ import annotations


def test_tool_catalog_covers_registered_and_skill_tools():
    from core.teammate.skills import SkillRegistry
    from core.teammate.tool_fabric import tool_catalog

    skills = SkillRegistry()
    skills.seed_default_15()
    cat = tool_catalog(skills)

    assert cat["schema"] == 1
    assert cat["count"] > 0
    tools = cat["tools"]

    # skill-granted tool carries permissions + the skill that uses it
    assert "git" in tools
    assert "inspect_repository" in tools["git"]["used_by_skills"]
    assert tools["git"]["permissions"]["filesystem"] == ["repo"]
    assert tools["git"]["environments"] == ["production"]

    # risk is derived conservatively from the granting skill's level
    assert tools["edit"]["risk"] == "controlled"      # write_code = medium
    assert tools["deploy"]["risk"] == "high_risk"     # deploy_service = high
    assert tools["rollback"]["risk"] == "high_risk"   # rollback_service critical

    # forbidden tools are recorded with the forbidding skill
    assert "write_code" in tools["rm"]["forbidden_by"]

    # the authoritative KAI tool-bus specs are merged in with full schema
    registered = [t for t in tools.values() if t["registered"]]
    assert registered, "no registered kai.* tools merged into the catalog"
    assert all("auth_required" in t and "audit" in t and "rollback" in t
               for t in registered)
