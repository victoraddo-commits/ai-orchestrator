import pytest

from core.teammate.registry import TeammateRegistry
from core.teammate.skills import SEED_SKILL_IDS, SkillRecord, SkillRegistry


def _skill(**over):
    base = dict(
        skill_id="inspect_repository",
        name="Inspect Repository",
        description="Survey a git repo layout and state.",
        inputs=["repo_path"],
        outputs=["report"],
        required_capabilities=["inspect"],
        required_permissions={"secrets": [], "network": [], "filesystem": ["repo"]},
        allowed_tools=["git"],
        forbidden_tools=[],
        model_requirements={"capability": "generate"},
        security_requirements={"level": "low"},
        verification_method="manual_review",
        timeout=300,
        resource_requirements={"cpu": 1, "memory": "512mb"},
        version=1,
        dependencies=[],
    )
    base.update(over)
    return SkillRecord(**base)


def _fresh_registry():
    return SkillRegistry()


def test_register_and_get_roundtrip():
    reg = _fresh_registry()
    rec = _skill()
    reg.register(rec)
    assert reg.get("inspect_repository") == rec


def test_register_duplicate_raises():
    reg = _fresh_registry()
    reg.register(_skill())
    with pytest.raises(ValueError):
        reg.register(_skill())


def test_list_empty_and_after_register():
    reg = _fresh_registry()
    assert reg.list() == []
    reg.register(_skill())
    assert [s.skill_id for s in reg.list()] == ["inspect_repository"]


def test_persists_across_instances():
    r1 = _fresh_registry()
    r1.register(_skill())
    r2 = _fresh_registry()  # new instance, same memory dir (isolated_memory fixture)
    assert r2.get("inspect_repository") is not None
    assert r2.get("inspect_repository").name == "Inspect Repository"


def test_seed_default_15_idempotent():
    reg = _fresh_registry()
    assert reg.seed_default_15() == 15
    assert reg.seed_default_15() == 0   # already present, no-op
    assert len(reg.list()) == 15


def test_seed_contains_exact_skill_ids():
    reg = _fresh_registry()
    reg.seed_default_15()
    assert sorted(s.skill_id for s in reg.list()) == sorted(SEED_SKILL_IDS)


def test_seed_write_code_forbids_destructive_tools():
    reg = _fresh_registry()
    reg.seed_default_15()
    wc = reg.get("write_code")
    assert "kai_coder" in wc.model_requirements.get("roles", [])
    assert any(t in wc.forbidden_tools for t in ("rm", "drop", "truncate"))


def test_seed_all_validate_clean():
    reg = _fresh_registry()
    reg.seed_default_15()
    for sid in SEED_SKILL_IDS:
        assert reg.validate(sid) == [], (sid, reg.validate(sid))


def test_validate_catches_bad_skill():
    reg = _fresh_registry()
    bad = _skill(required_capabilities=[], timeout=-5)
    reg.register(bad)
    problems = reg.validate("inspect_repository")
    assert "required_capabilities must not be empty" in problems
    assert "timeout must be positive" in problems


def test_add_skill_seam_links_skill_id_to_teammate():
    skilled = _fresh_registry()
    skilled.seed_default_15()
    mates = TeammateRegistry()
    mate = mates.create({"name": "arch", "specialization": "architect",
                         "capabilities": ["inspect"], "skills": []})
    assert mates.add_skill(mate.id, "inspect_repository", skilled) is True
    updated = mates.get(mate.id)
    assert "inspect_repository" in updated.skills
    # idempotent — second add is a no-op
    assert mates.add_skill(mate.id, "inspect_repository", skilled) is False


def test_add_skill_rejects_unknown_skill():
    skilled = _fresh_registry()
    mates = TeammateRegistry()
    mate = mates.create({"name": "arch", "specialization": "architect",
                         "capabilities": [], "skills": []})
    assert mates.add_skill(mate.id, "nope", skilled) is False
    assert mates.get(mate.id).skills == []
