"""§18 Resource Governor — bounded workforce growth + live snapshot."""
from __future__ import annotations

import pytest

from core.teammate.factory import Factory
from core.teammate.registry import TeammateRegistry
from core.workforce.resource_governor import ResourceExhausted, ResourceGovernor

SPEC = {
    "specialization": "coder",
    "required_skills": ["write_code"],
    "capabilities": ["code", "write"],
}


def _mk(reg, n=1, spec="coder"):
    for i in range(n):
        reg.create({"name": f"{spec}-{i}", "specialization": spec,
                    "skills": ["write_code"], "capabilities": ["code", "write"]})


def test_can_spawn_under_and_at_cap():
    reg = TeammateRegistry()
    gov = ResourceGovernor(max_workers=2)
    _mk(reg, 1)
    assert gov.can_spawn(reg)[0] is True
    _mk(reg, 1, spec="qa")
    allowed, reason = gov.can_spawn(reg)
    assert allowed is False
    assert "cap" in reason


def test_retired_workers_do_not_count_against_cap():
    reg = TeammateRegistry()
    gov = ResourceGovernor(max_workers=1)
    t = reg.create({"name": "old", "specialization": "coder",
                    "skills": [], "capabilities": []})
    assert gov.can_spawn(reg)[0] is False
    reg.retire(t.id, "test cleanup")
    assert gov.can_spawn(reg)[0] is True


def test_snapshot_reports_workers_queue_gpu_and_host():
    reg = TeammateRegistry()
    gov = ResourceGovernor(max_workers=5)
    _mk(reg, 2)
    snap = gov.snapshot(registry=reg,
                        missions=[{"status": "RUNNING"}, {"status": "COMPLETED"}])
    assert snap["max_workers"] == 5
    assert snap["workers"]["active"] == 2
    assert snap["queue_depth"] == 1
    assert "gpu" in snap and "host" in snap
    assert snap["can_spawn"] is True
    assert snap["schema"] == 1


def test_factory_refuses_new_worker_at_cap_but_still_reuses():
    reg = TeammateRegistry()
    gov = ResourceGovernor(max_workers=1)
    factory = Factory(reg, governor=gov)

    first = factory.create_from_requirement(dict(SPEC))
    assert first.created is True
    # same requirement reuses the existing READY teammate → no growth
    again = factory.create_from_requirement(dict(SPEC))
    assert again.created is False
    assert again.teammate.id == first.teammate.id

    # a genuinely new worker would exceed the cap → refused
    with pytest.raises(ResourceExhausted):
        factory.create_from_requirement({
            "specialization": "reviewer",
            "required_skills": ["inspect_repository"],
            "capabilities": ["inspect"],
        })
