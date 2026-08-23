"""Bootstrap registration: existing components appear in the registry."""
from unittest import mock

import core.workforce.bootstrap as bootstrap
from core.workforce import registry


def _reset():
    registry._save_all({"schema_version": 1, "records": []})


def test_providers_register_with_dev_guard():
    _reset()
    provs = {
        "gpuai_minimax": {"description": "GPU.ai", "available": True,
                          "enabled": True, "capabilities": ["text_task"],
                          "cost_tier": "paid"},
        "ox_alpha": {"description": "dev workhorse", "available": True,
                     "enabled": True, "capabilities": ["text_task"],
                     "cost_tier": "free"},
    }
    with mock.patch.object(bootstrap.ai_provider, "list_providers",
                           return_value=provs):
        bootstrap.sync_providers()
    ox = registry.get("provider:ox_alpha")
    normal = registry.get("provider:gpuai_minimax")
    assert normal.environment == "production"
    assert ox.environment == "development"
    assert ox.temporary is True
    assert ox.permissions["secrets"] == []      # dev workers: no secrets, ever


def test_pool_slots_register():
    _reset()
    bootstrap.sync_pool_slots(max_concurrent=4)
    ids = {w.worker_id for w in registry.list_workers(kind="pool_worker")}
    assert ids == {"pool-worker-0", "pool-worker-1", "pool-worker-2",
                   "pool-worker-3"}
    slot = registry.get("pool-worker-0")
    assert slot.limits["max_concurrency"] == 1
    assert slot.capabilities == ["generate", "review", "deploy"]


def test_roles_register_from_agent_roles():
    _reset()
    with mock.patch.object(bootstrap, "_discover_role_task_types",
                           return_value={"architecture_agent": ["coding"],
                                         "fast_analysis_agent": ["log_analysis"]}):
        bootstrap.sync_roles()
    role = registry.get("role:architecture_agent")
    assert role is not None
    assert role.capabilities == ["coding"]
    assert role.environment == "production"


def test_sync_is_idempotent():
    _reset()
    with mock.patch.object(bootstrap.ai_provider, "list_providers",
                           return_value={}):
        bootstrap.sync_providers()
        bootstrap.sync_providers()   # must not duplicate or raise
    assert registry.list_workers() == []
