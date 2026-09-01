"""WorkerRecord + registry persistence tests. Uses conftest's isolated_memory
fixture automatically (memory writes land in tmp_path during tests)."""
import pytest

from core.workforce.registry import (
    WorkerRecord, register, get, list_workers, update_status,
    record_heartbeat, deregister_expired, REGISTRY_FILE,
)


def _rec(**over):
    base = dict(
        worker_id="provider:testprov", kind="provider",
        capabilities=["generate"], permissions={"secrets": [], "network": [], "filesystem": []},
        limits={"max_concurrency": 1, "timeout_seconds": 60},
        environment="production", temporary=False,
    )
    base.update(over)
    return WorkerRecord(**base)


def test_register_and_get_roundtrip():
    register(_rec())
    loaded = get("provider:testprov")
    assert loaded is not None
    assert loaded.kind == "provider"
    assert loaded.status == "idle"
    assert loaded.health["consecutive_failures"] == 0


def test_register_upsert_preserves_status():
    register(_rec())
    update_status("provider:testprov", "degraded", reason="test")
    register(_rec())
    assert get("provider:testprov").status == "degraded"


def test_update_status_records_history_and_reason():
    register(_rec())
    update_status("provider:testprov", "dead", reason="3 consecutive failures")
    rec = get("provider:testprov")
    assert rec.status == "dead"
    assert rec.health["last_reason"] == "3 consecutive failures"
    assert len(rec.health["transitions"]) == 1
    assert rec.health["transitions"][0]["to"] == "dead"


def test_record_heartbeat_resets_failures():
    register(_rec())
    update_status("provider:testprov", "degraded", reason="x",
                  increment_failures=True)
    assert get("provider:testprov").health["consecutive_failures"] == 1
    record_heartbeat("provider:testprov")
    rec = get("provider:testprov")
    assert rec.health["consecutive_failures"] == 0
    assert rec.health["last_heartbeat"] is not None


def test_list_workers_filters():
    register(_rec(worker_id="p:a", kind="provider"))
    register(_rec(worker_id="r:b", kind="role", environment="development"))
    prod = list_workers(environment="production")
    assert [w.worker_id for w in prod] == ["p:a"]
    assert len(list_workers()) == 2


def test_deregister_expired_temporary():
    register(_rec(worker_id="t:x", temporary=True,
                  expires_at="2000-01-01T00:00:00+00:00"))
    register(_rec(worker_id="t:y", temporary=True,
                  expires_at="2999-01-01T00:00:00+00:00"))
    removed = deregister_expired()
    assert removed == ["t:x"]
    assert get("t:x") is None
    assert get("t:y") is not None


def test_unknown_worker_get_returns_none():
    assert get("nope") is None


def test_registry_file_is_workers_json():
    assert REGISTRY_FILE == "workers.json"


def test_worker_record_new_fields_have_defaults():
    """New fields have correct default values."""
    from core.workforce.registry import WorkerRecord
    rec = WorkerRecord(
        worker_id="test:fields",
        kind="provider",
        capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []},
        limits={},
    )
    assert rec.tools == []
    assert rec.data_scope == []
    assert rec.vault_scope == []
    assert rec.destructive_authority["delete_files"] is False
    assert rec.destructive_authority["admin_action"] is False
    assert len(rec.destructive_authority) == 8


def test_worker_record_new_fields_serializable():
    """New fields round-trip through to_dict/from_dict."""
    from core.workforce.registry import WorkerRecord
    rec = WorkerRecord(
        worker_id="test:serialize",
        kind="role",
        capabilities=["planning"],
        permissions={"secrets": [], "network": [], "filesystem": []},
        limits={},
        tools=["bash", "read_file"],
        data_scope=["logs", "configs"],
        vault_scope=["kai-betting/"],
        destructive_authority={"delete_files": True, "terminate_worker": False,
                              "kill_provider": False, "force_deploy": False,
                              "modify_secrets": False, "network_bridge": False,
                              "data_export": False, "admin_action": False},
    )
    d = rec.to_dict()
    assert d["tools"] == ["bash", "read_file"]
    assert d["data_scope"] == ["logs", "configs"]
    assert d["vault_scope"] == ["kai-betting/"]
    assert d["destructive_authority"]["delete_files"] is True

    loaded = WorkerRecord.from_dict(d)
    assert loaded.tools == ["bash", "read_file"]
    assert loaded.data_scope == ["logs", "configs"]
    assert loaded.destructive_authority["delete_files"] is True


def test_register_and_get_preserves_new_fields():
    """New fields survive register/get round-trip."""
    register(_rec(worker_id="test:roundtrip",
                  tools=["grep"],
                  data_scope=["configs"],
                  vault_scope=["secrets/"],
                  destructive_authority={"delete_files": False,
                                        "terminate_worker": False,
                                        "kill_provider": False,
                                        "force_deploy": False,
                                        "modify_secrets": False,
                                        "network_bridge": False,
                                        "data_export": False,
                                        "admin_action": False}))
    loaded = get("test:roundtrip")
    assert loaded.tools == ["grep"]
    assert loaded.data_scope == ["configs"]
    assert loaded.vault_scope == ["secrets/"]
