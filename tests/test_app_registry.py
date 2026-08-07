"""Phase 19R: Application Registry tests."""

import json
import pytest
from fastapi.testclient import TestClient

from core.app_registry_models import (
    AppRecord,
    AppCreate,
    AppUpdate,
    RegistryFile,
    RegistryStatus,
    _now_iso,
)
from core.app_registry import (
    create_entry,
    get_entry,
    list_entries,
    update_entry,
    delete_entry,
    update_status,
    set_deployed_url,
    update_metadata,
    health_check,
    DuplicateAppName,
    AppNotFound,
    CURRENT_SCHEMA_VERSION,
)
from core.api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_registry_file(isolated_memory):
    """Each test starts with no registry file on disk."""
    registry_path = isolated_memory / "app_registry.json"
    if registry_path.exists():
        registry_path.unlink()
    yield


# ── Pydantic model tests ─────────────────────────────────────────────────


def test_registry_status_enum_values():
    assert RegistryStatus.registered.value == "registered"
    assert RegistryStatus.building.value == "building"
    assert RegistryStatus.deployed.value == "deployed"
    assert RegistryStatus.failed.value == "failed"
    assert RegistryStatus.archived.value == "archived"


def test_app_create_validates_minimum():
    app = AppCreate(app_name="test-app", repo="https://github.com/test/app")
    assert app.app_name == "test-app"
    assert app.repo == "https://github.com/test/app"
    assert app.status == RegistryStatus.registered


def test_app_create_default_metadata():
    app = AppCreate(app_name="my-app", repo=".")
    assert app.metadata == {}


def test_app_create_with_metadata():
    app = AppCreate(
        app_name="my-app", repo="https://example.com/repo",
        metadata={"team": "platform", "env": "prod"}
    )
    assert app.metadata["team"] == "platform"


def test_app_create_rejects_empty_name():
    with pytest.raises(Exception):
        AppCreate(app_name="", repo=".")


def test_app_update_all_optional():
    update = AppUpdate()
    assert update.status is None
    assert update.deployed_url is None
    assert update.repo is None
    assert update.metadata is None


def test_app_update_partial_fields():
    update = AppUpdate(status=RegistryStatus.building)
    assert update.status == RegistryStatus.building
    assert update.deployed_url is None


def test_registry_file_defaults():
    rf = RegistryFile()
    assert rf.schema_version == 1
    assert rf.records == []


def test_app_record_serialization():
    record = AppRecord(
        id="abc123", app_name="test", repo="https://github.com/test/test",
        status=RegistryStatus.registered,
        created_at=_now_iso(), updated_at=_now_iso(),
    )
    d = record.model_dump(mode="json")
    assert d["id"] == "abc123"
    assert d["app_name"] == "test"
    assert d["status"] == "registered"


def test_registry_file_serialization():
    record = AppRecord(
        id="r1", app_name="a", repo=".",
        status=RegistryStatus.registered,
        created_at=_now_iso(), updated_at=_now_iso(),
    )
    rf = RegistryFile(schema_version=1, records=[record])
    d = rf.model_dump(mode="json")
    assert d["schema_version"] == 1
    assert len(d["records"]) == 1
    assert d["records"][0]["id"] == "r1"


# ── Storage layer unit tests ────────────────────────────────────────────


def test_create_entry_basic():
    record = create_entry(AppCreate(app_name="hello-world", repo="https://github.com/kai/hello"))
    assert record.app_name == "hello-world"
    assert record.repo == "https://github.com/kai/hello"
    assert record.status == RegistryStatus.registered
    assert record.id
    assert len(record.id) == 8  # generate_id produces 8-char hex
    assert record.created_at
    assert record.updated_at
    assert record.metadata == {}


def test_get_entry_returns_correct():
    created = create_entry(AppCreate(app_name="demo", repo="https://github.com/kai/demo"))
    fetched = get_entry(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.app_name == "demo"


def test_get_entry_missing_returns_none():
    assert get_entry("nonexistent") is None


def test_list_entries_empty():
    results = list_entries()
    assert results == []


def test_list_entries_with_filtering():
    create_entry(AppCreate(app_name="alpha", repo="a"))
    create_entry(AppCreate(app_name="beta", repo="b"))
    create_entry(AppCreate(app_name="gamma", repo="c"))

    all_results = list_entries()
    assert len(all_results) == 3

    search_results = list_entries(search="beta")
    assert len(search_results) == 1
    assert search_results[0].app_name == "beta"

    repo_results = list_entries(search="b")
    assert len(repo_results) == 1  # only "beta" whose repo="b" contains "b"


def test_list_entries_status_filter():
    create_entry(AppCreate(app_name="a1", repo="."))
    create_entry(AppCreate(app_name="a2", repo=".", status=RegistryStatus.building))

    registered = list_entries(status="registered")
    assert len(registered) == 1
    assert registered[0].app_name == "a1"

    building = list_entries(status="building")
    assert len(building) == 1
    assert building[0].app_name == "a2"


def test_list_entries_metadata_filter():
    create_entry(AppCreate(app_name="m1", repo=".", metadata={"team": "platform"}))
    create_entry(AppCreate(app_name="m2", repo=".", metadata={"team": "infra"}))
    create_entry(AppCreate(app_name="m3", repo=".", metadata={"team": "platform", "env": "prod"}))

    results = list_entries(metadata_filter={"team": "platform"})
    assert len(results) == 2
    names = {r.app_name for r in results}
    assert names == {"m1", "m3"}


def test_list_entries_pagination():
    for i in range(25):
        create_entry(AppCreate(app_name=f"app-{i:02d}", repo=f"r{i}"))

    page1 = list_entries(limit=10, offset=0)
    assert len(page1) == 10

    page2 = list_entries(limit=10, offset=10)
    assert len(page2) == 10

    page3 = list_entries(limit=10, offset=20)
    assert len(page3) == 5

    assert page1[0].app_name != page2[0].app_name


def test_list_entries_sort_by_updated_at():
    a = create_entry(AppCreate(app_name="first", repo="."))
    import time
    time.sleep(0.01)
    b = create_entry(AppCreate(app_name="second", repo="."))

    results = list_entries(sort="created_at")
    assert results[0].app_name == "second"
    assert results[1].app_name == "first"


def test_update_entry_status():
    created = create_entry(AppCreate(app_name="to-update", repo="."))
    updated = update_entry(created.id, AppUpdate(status=RegistryStatus.deployed))
    assert updated is not None
    assert updated.status == RegistryStatus.deployed


def test_update_entry_deployed_url():
    created = create_entry(AppCreate(app_name="to-url", repo="."))
    updated = update_entry(created.id, AppUpdate(deployed_url="https://app.example.com"))
    assert updated.deployed_url == "https://app.example.com"


def test_update_entry_metadata():
    created = create_entry(AppCreate(app_name="to-meta", repo=".", metadata={"foo": "bar"}))
    updated = update_entry(created.id, AppUpdate(metadata={"baz": "qux"}))
    assert updated.metadata == {"foo": "bar", "baz": "qux"}


def test_update_entry_metadata_overwrite():
    created = create_entry(AppCreate(app_name="to-ow", repo=".", metadata={"key": "old"}))
    updated = update_entry(created.id, AppUpdate(metadata={"key": "new"}))
    assert updated.metadata == {"key": "new"}


def test_update_entry_missing_raises():
    with pytest.raises(AppNotFound):
        update_entry("nonexistent", AppUpdate(status=RegistryStatus.deployed))


def test_delete_entry_archives():
    created = create_entry(AppCreate(app_name="to-delete", repo="."))
    archived = delete_entry(created.id)
    assert archived.status == RegistryStatus.archived

    fetched = get_entry(created.id)
    assert fetched is not None
    assert fetched.status == RegistryStatus.archived


def test_delete_missing_raises():
    with pytest.raises(AppNotFound):
        delete_entry("nonexistent")


def test_convenience_update_status():
    created = create_entry(AppCreate(app_name="conv", repo="."))
    result = update_status(created.id, RegistryStatus.failed)
    assert result.status == RegistryStatus.failed


def test_convenience_set_deployed_url():
    created = create_entry(AppCreate(app_name="conv-url", repo="."))
    result = set_deployed_url(created.id, "https://deployed.example.com")
    assert result.deployed_url == "https://deployed.example.com"


def test_convenience_update_metadata():
    created = create_entry(AppCreate(app_name="conv-meta", repo="."))
    result = update_metadata(created.id, {"tag": "v1"})
    assert result.metadata["tag"] == "v1"


def test_duplicate_app_name_rejected():
    create_entry(AppCreate(app_name="unique-one", repo="."))
    with pytest.raises(DuplicateAppName):
        create_entry(AppCreate(app_name="unique-one", repo="other"))

    # Duplicate of archived is allowed
    first = create_entry(AppCreate(app_name="dup-archived", repo="."))
    delete_entry(first.id)
    second = create_entry(AppCreate(app_name="dup-archived", repo="."))
    assert second.app_name == "dup-archived"


def test_health_check_ok():
    assert health_check() is True

    create_entry(AppCreate(app_name="health-test", repo="."))
    assert health_check() is True


def test_persistence_survives_reload():
    created = create_entry(AppCreate(app_name="persist", repo="https://github.com/persist"))
    fetched = get_entry(created.id)
    assert fetched is not None
    assert fetched.app_name == "persist"
    assert fetched.repo == "https://github.com/persist"


# ── API endpoint tests ──────────────────────────────────────────────────


def test_api_create_app():
    resp = client.post("/api/v1/registry/apps", json={
        "app_name": "api-test",
        "repo": "https://github.com/api/test",
        "metadata": {"team": "api-team"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["app_name"] == "api-test"
    assert data["repo"] == "https://github.com/api/test"
    assert data["metadata"]["team"] == "api-team"
    assert data["status"] == "registered"
    assert data["id"]


def test_api_list_apps():
    client.post("/api/v1/registry/apps", json={"app_name": "list-a", "repo": "https://a"})
    client.post("/api/v1/registry/apps", json={"app_name": "list-b", "repo": "https://b"})

    resp = client.get("/api/v1/registry/apps")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    names = {r["app_name"] for r in data}
    assert "list-a" in names
    assert "list-b" in names


def test_api_list_apps_with_status_filter():
    client.post("/api/v1/registry/apps", json={"app_name": "filter-reg", "repo": "."})
    resp = client.post("/api/v1/registry/apps", json={
        "app_name": "filter-build", "repo": ".", "status": "building"
    })
    build_id = resp.json()["id"]

    resp = client.get("/api/v1/registry/apps?status=registered")
    data = resp.json()
    assert all(r["status"] == "registered" for r in data)

    resp = client.get("/api/v1/registry/apps?status=building")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == build_id


def test_api_list_apps_with_search():
    client.post("/api/v1/registry/apps", json={"app_name": "searchable-app", "repo": "https://special-repo"})
    resp = client.get("/api/v1/registry/apps?search=searchable")
    data = resp.json()
    assert len(data) >= 1
    assert any(r["app_name"] == "searchable-app" for r in data)


def test_api_list_apps_with_metadata_filter():
    client.post("/api/v1/registry/apps", json={
        "app_name": "meta-app", "repo": ".", "metadata": {"org": "kai"}
    })
    client.post("/api/v1/registry/apps", json={
        "app_name": "non-meta", "repo": ".", "metadata": {"org": "other"}
    })

    resp = client.get('/api/v1/registry/apps?metadata={"org":"kai"}')
    data = resp.json()
    assert len(data) == 1
    assert data[0]["app_name"] == "meta-app"


def test_api_list_apps_pagination():
    for i in range(5):
        client.post("/api/v1/registry/apps", json={"app_name": f"page-{i}", "repo": "."})

    resp = client.get("/api/v1/registry/apps?limit=2&offset=0")
    assert len(resp.json()) == 2

    resp = client.get("/api/v1/registry/apps?limit=2&offset=2")
    assert len(resp.json()) == 2

    resp = client.get("/api/v1/registry/apps?limit=2&offset=4")
    assert len(resp.json()) == 1


def test_api_get_app():
    resp = client.post("/api/v1/registry/apps", json={"app_name": "get-me", "repo": "."})
    app_id = resp.json()["id"]

    resp = client.get(f"/api/v1/registry/apps/{app_id}")
    assert resp.status_code == 200
    assert resp.json()["app_name"] == "get-me"


def test_api_get_app_404():
    resp = client.get("/api/v1/registry/apps/nonexistent")
    assert resp.status_code == 404


def test_api_update_app():
    resp = client.post("/api/v1/registry/apps", json={"app_name": "patch-me", "repo": "."})
    app_id = resp.json()["id"]

    resp = client.patch(f"/api/v1/registry/apps/{app_id}", json={
        "status": "deployed",
        "deployed_url": "https://live.example.com",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deployed"
    assert data["deployed_url"] == "https://live.example.com"


def test_api_update_app_metadata_merge():
    resp = client.post("/api/v1/registry/apps", json={
        "app_name": "meta-merge", "repo": ".", "metadata": {"a": 1}
    })
    app_id = resp.json()["id"]

    resp = client.patch(f"/api/v1/registry/apps/{app_id}", json={
        "metadata": {"b": 2}
    })
    data = resp.json()
    assert data["metadata"] == {"a": 1, "b": 2}


def test_api_update_app_404():
    resp = client.patch("/api/v1/registry/apps/nonexistent", json={"status": "deployed"})
    assert resp.status_code == 404


def test_api_delete_app():
    resp = client.post("/api/v1/registry/apps", json={"app_name": "del-me", "repo": "."})
    app_id = resp.json()["id"]

    resp = client.delete(f"/api/v1/registry/apps/{app_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # Verify it's still retrievable but archived
    resp = client.get(f"/api/v1/registry/apps/{app_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


def test_api_delete_app_404():
    resp = client.delete("/api/v1/registry/apps/nonexistent")
    assert resp.status_code == 404


def test_api_duplicate_app_name_409():
    client.post("/api/v1/registry/apps", json={"app_name": "dup-name", "repo": "a"})
    resp = client.post("/api/v1/registry/apps", json={"app_name": "dup-name", "repo": "b"})
    assert resp.status_code == 409


def test_api_registry_health():
    resp = client.get("/api/v1/registry/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["healthy"] is True


def test_api_validation_empty_name():
    resp = client.post("/api/v1/registry/apps", json={"app_name": "", "repo": "."})
    assert resp.status_code == 422


def test_api_validation_missing_repo():
    resp = client.post("/api/v1/registry/apps", json={"app_name": "no-repo"})
    assert resp.status_code == 422


def test_api_list_sorted_by_updated_at():
    a = client.post("/api/v1/registry/apps", json={"app_name": "sort-a", "repo": "."})
    b = client.post("/api/v1/registry/apps", json={"app_name": "sort-b", "repo": "."})

    # Update a
    aid = a.json()["id"]
    client.patch(f"/api/v1/registry/apps/{aid}", json={"status": "building"})

    resp = client.get("/api/v1/registry/apps?sort=updated_at")
    data = resp.json()
    a_idx = next(i for i, r in enumerate(data) if r["id"] == aid)
    b_idx = next(i for i, r in enumerate(data) if r["id"] == b.json()["id"])
    assert a_idx < b_idx


# ── Full lifecycle test ─────────────────────────────────────────────────


def test_full_lifecycle_hook_scenario():
    """Simulate the build lifecycle hook pattern described in the plan."""

    # 1. Register
    resp = client.post("/api/v1/registry/apps", json={
        "app_name": "lifecycle-app",
        "repo": "https://github.com/kai/lifecycle",
        "metadata": {"build_id": "build_123", "created_by": "kai"},
    })
    assert resp.status_code == 201
    app_id = resp.json()["id"]

    # 2. Build begins
    resp = client.patch(f"/api/v1/registry/apps/{app_id}", json={"status": "building"})
    assert resp.json()["status"] == "building"

    # 3. Deploy succeeds
    resp = client.patch(f"/api/v1/registry/apps/{app_id}", json={
        "status": "deployed",
        "deployed_url": "https://lifecycle.example.com",
    })
    assert resp.json()["status"] == "deployed"
    assert resp.json()["deployed_url"] == "https://lifecycle.example.com"

    # Verify final state
    resp = client.get(f"/api/v1/registry/apps/{app_id}")
    data = resp.json()
    assert data["status"] == "deployed"
    assert data["deployed_url"] == "https://lifecycle.example.com"
    assert data["metadata"]["build_id"] == "build_123"

    # 4. Simulate failure on another app
    resp = client.post("/api/v1/registry/apps", json={
        "app_name": "failing-app", "repo": "https://github.com/kai/failing",
    })
    fid = resp.json()["id"]

    resp = client.patch(f"/api/v1/registry/apps/{fid}", json={
        "status": "failed",
        "metadata": {"error": "build timeout", "build_id": "build_456"},
    })
    assert resp.json()["status"] == "failed"
    assert resp.json()["metadata"]["error"] == "build timeout"
