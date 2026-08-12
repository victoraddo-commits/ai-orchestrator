"""Phase 19A: module auto-registry for Kai Command Center."""

import json
import os
import pytest
from fastapi.testclient import TestClient

from core.module_registry import (
    ModuleRegistry,
    load_modules,
    get_registered_modules,
    register_module,
    reset,
    DEFAULT_MODULES_DIR,
)
from core.api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_registry():
    reset()
    yield
    reset()


# ── Direct registry unit tests ──────────────────────────────────────────────


def test_load_modules_from_empty_directory(tmp_path):
    mod_dir = tmp_path / "empty"
    mod_dir.mkdir()
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert modules == {}


def test_load_modules_from_missing_directory(tmp_path):
    mod_dir = tmp_path / "nonexistent"
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert modules == {}


def test_load_modules_loads_valid_descriptor(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "test-module.json").write_text(json.dumps({
        "name": "test-module",
        "version": "1.0.0",
        "description": "A test module",
        "endpoints": ["/api/test"],
        "capabilities": ["testing"],
        "dependencies": [],
    }))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert "test-module" in modules
    mod = modules["test-module"]
    assert mod["name"] == "test-module"
    assert mod["version"] == "1.0.0"
    assert mod["description"] == "A test module"
    assert mod["endpoints"] == ["/api/test"]
    assert mod["capabilities"] == ["testing"]
    assert mod["dependencies"] == []
    assert mod["source_file"] == "test-module.json"


def test_load_modules_loads_multiple_descriptors(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "alpha.json").write_text(json.dumps({
        "name": "alpha", "version": "1.0", "description": "Alpha module",
        "endpoints": [], "capabilities": [], "dependencies": [],
    }))
    (mod_dir / "beta.json").write_text(json.dumps({
        "name": "beta", "version": "2.0", "description": "Beta module",
        "endpoints": [], "capabilities": [], "dependencies": [],
    }))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert len(modules) == 2
    assert "alpha" in modules
    assert "beta" in modules


def test_load_modules_skips_non_json_files(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "valid.json").write_text(json.dumps({
        "name": "valid", "version": "1.0", "description": "ok",
    }))
    (mod_dir / "readme.txt").write_text("not json")
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert len(modules) == 1
    assert "valid" in modules


def test_load_modules_skips_malformed_json(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "broken.json").write_text("{not valid: json}")
    (mod_dir / "valid.json").write_text(json.dumps({
        "name": "valid", "version": "1.0", "description": "ok",
    }))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert "valid" in modules
    assert "broken" not in modules
    assert len(modules) == 1


def test_load_modules_skips_non_object_json(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "list.json").write_text(json.dumps([1, 2, 3]))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert modules == {}


def test_load_modules_skips_missing_required_fields(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "no_name.json").write_text(json.dumps({
        "version": "1.0", "description": "missing name",
    }))
    (mod_dir / "no_version.json").write_text(json.dumps({
        "name": "test", "description": "missing version",
    }))
    (mod_dir / "no_desc.json").write_text(json.dumps({
        "name": "test", "version": "1.0",
    }))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert modules == {}


def test_load_modules_skips_invalid_name_field(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "bad_name.json").write_text(json.dumps({
        "name": 42, "version": "1.0", "description": "numeric name",
    }))
    (mod_dir / "empty_name.json").write_text(json.dumps({
        "name": "   ", "version": "1.0", "description": "whitespace only",
    }))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert modules == {}


def test_load_modules_first_duplicate_name_wins(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "first.json").write_text(json.dumps({
        "name": "dup", "version": "1.0", "description": "first",
    }))
    (mod_dir / "second.json").write_text(json.dumps({
        "name": "dup", "version": "2.0", "description": "second",
    }))
    registry = ModuleRegistry(config_dir=str(mod_dir))
    modules = registry.load_modules()
    assert "dup" in modules
    assert modules["dup"]["version"] == "1.0"
    assert modules["dup"]["description"] == "first"
    assert modules["dup"]["source_file"] == "first.json"


def test_get_registered_modules_loads_on_first_call(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "auto.json").write_text(json.dumps({
        "name": "auto", "version": "1.0", "description": "auto loaded",
    }))
    reset()
    import core.module_registry as mr
    mr._module_registry._config_dir = str(mod_dir)
    mr._module_registry._modules = {}
    result = mr.get_registered_modules()
    assert "auto" in result
    mr._module_registry._config_dir = DEFAULT_MODULES_DIR
    mr._module_registry._modules = {}
    load_modules(DEFAULT_MODULES_DIR)


def test_register_module_programmatically():
    register_module(
        "dynamic-module", "0.1.0", "Dynamically registered",
        endpoints=["/api/dynamic"],
        capabilities=["dynamic"],
        dependencies=["some-dep"],
    )
    modules = get_registered_modules()
    assert "dynamic-module" in modules
    mod = modules["dynamic-module"]
    assert mod["version"] == "0.1.0"
    assert mod["endpoints"] == ["/api/dynamic"]
    assert mod["capabilities"] == ["dynamic"]
    assert mod["dependencies"] == ["some-dep"]


def test_register_module_default_empty_lists():
    register_module("simple", "1.0", "No extras")
    mod = get_registered_modules()["simple"]
    assert mod["endpoints"] == []
    assert mod["capabilities"] == []
    assert mod["dependencies"] == []


# ── API endpoint tests ──────────────────────────────────────────────────────


def test_api_modules_endpoint_returns_200():
    response = client.get("/api/modules")
    assert response.status_code == 200


def test_api_modules_endpoint_returns_json():
    response = client.get("/api/modules")
    assert response.headers["content-type"].startswith("application/json")


def test_api_modules_has_modules_key():
    body = client.get("/api/modules").json()
    assert "modules" in body
    assert isinstance(body["modules"], list)


def test_api_modules_auto_discovers_new_descriptor(tmp_path, monkeypatch):
    """New module descriptor placed in the scan directory auto-appears in /api/modules."""
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "new-module.json").write_text(json.dumps({
        "name": "new-module",
        "version": "1.0.0",
        "description": "Auto-discovered module",
        "endpoints": ["/api/new"],
        "capabilities": ["auto-discovery"],
        "dependencies": [],
    }))

    reset()
    import core.module_registry as mr
    old_dir = mr._module_registry._config_dir
    old_modules = mr._module_registry._modules.copy()
    try:
        mr._module_registry._config_dir = str(mod_dir)
        mr._module_registry._modules = {}
        body = client.get("/api/modules").json()
        mod = next((m for m in body["modules"] if m["name"] == "new-module"), None)
        assert mod is not None, f"new-module not found in {body['modules']}"
        assert mod["description"] == "Auto-discovered module"
        assert mod["version"] == "1.0.0"
        assert mod["capabilities"] == ["auto-discovery"]
    finally:
        mr._module_registry._config_dir = old_dir
        mr._module_registry._modules = old_modules
        load_modules(old_dir)


def test_api_modules_empty_directory_returns_empty_list(tmp_path):
    mod_dir = tmp_path / "empty"
    mod_dir.mkdir()

    reset()
    import core.module_registry as mr
    old_dir = mr._module_registry._config_dir
    old_modules = mr._module_registry._modules.copy()
    try:
        mr._module_registry._config_dir = str(mod_dir)
        mr._module_registry._modules = {}
        body = client.get("/api/modules").json()
        assert body["modules"] == []
    finally:
        mr._module_registry._config_dir = old_dir
        mr._module_registry._modules = old_modules
        load_modules(old_dir)


def test_api_modules_malformed_json_handled_gracefully(tmp_path):
    mod_dir = tmp_path / "modules"
    mod_dir.mkdir()
    (mod_dir / "broken.json").write_text("{this is not valid}}")
    (mod_dir / "valid.json").write_text(json.dumps({
        "name": "valid", "version": "1.0", "description": "good",
    }))

    reset()
    import core.module_registry as mr
    old_dir = mr._module_registry._config_dir
    old_modules = mr._module_registry._modules.copy()
    try:
        mr._module_registry._config_dir = str(mod_dir)
        mr._module_registry._modules = {}
        body = client.get("/api/modules").json()
        valid_mod = next((m for m in body["modules"] if m["name"] == "valid"), None)
        assert valid_mod is not None, f"'valid' module not found in {body['modules']}"
        assert len(body["modules"]) == 1
    finally:
        mr._module_registry._config_dir = old_dir
        mr._module_registry._modules = old_modules
        load_modules(old_dir)


def test_api_modules_returns_structured_module_data():
    """Each module in /api/modules has all required fields."""
    register_module("structured", "2.0.0", "Fully populated",
                    endpoints=["/a", "/b"],
                    capabilities=["cap1", "cap2"],
                    dependencies=["dep1"])
    body = client.get("/api/modules").json()
    mod = next((m for m in body["modules"] if m["name"] == "structured"), None)
    assert mod is not None, f"'structured' module not found"
    for field in ("name", "version", "description", "endpoints",
                  "capabilities", "dependencies"):
        assert field in mod, f"module missing field {field}"
    assert mod["endpoints"] == ["/a", "/b"]
    assert mod["capabilities"] == ["cap1", "cap2"]


def test_command_center_summary_includes_modules():
    """Phase 19A: /api/command-center/summary includes a 'modules' key."""
    response = client.get("/api/command-center/summary")
    assert response.status_code == 200
    body = response.json()
    assert "modules" in body, "command-center summary must include modules section"
    assert isinstance(body["modules"], dict)


def test_command_center_summary_modules_reflects_registry():
    """Adding a module via the registry shows in command-center summary."""
    register_module("summary-test", "1.0.0", "Shows in summary")
    body = client.get("/api/command-center/summary").json()
    assert "summary-test" in body["modules"]
