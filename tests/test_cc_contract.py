"""Command Center endpoint contract + no-raw-code guards.

These lock in the 2026-09-19 command-center-integrity directive: every endpoint
the CC HTML calls must exist in the API OpenAPI, and the panels that used to
dump raw JSON (<pre>{...}) must stay replaced with real UIs.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CC_HTML = ROOT / "core" / "kai" / "command_center.html"


def _load_contract_module():
    spec = importlib.util.spec_from_file_location(
        "cc_contract_check", ROOT / "scripts" / "cc_contract_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cc():
    return _load_contract_module()


@pytest.fixture(scope="module")
def html():
    return CC_HTML.read_text()


@pytest.fixture(scope="module")
def openapi_paths():
    from core.api import app
    return list(app.openapi().get("paths", {}).keys())


def test_every_called_endpoint_exists_in_openapi(cc, html, openapi_paths):
    endpoints = cc.extract_endpoints(html)
    assert endpoints, "no endpoints extracted from command_center.html"
    missing = cc.find_missing(endpoints, openapi_paths)
    assert missing == [], f"CC calls endpoints missing from OpenAPI: {missing}"


def test_dynamic_paths_still_extracted(cc, html):
    endpoints = cc.extract_endpoints(html)
    # The extraction must see interpolated paths, not just literals.
    assert any("@" in p for p in endpoints), "no runtime-interpolated paths found"


def test_no_generic_json_dump_renderer(cc, html):
    assert "loadJsonTab(" not in html, (
        "generic loadJsonTab raw-dump renderer must not return")


@pytest.mark.parametrize("fn", [
    "loadSecondBrain", "loadApprovals", "loadNotifications",
    "loadRoadmap", "loadLearning", "loadEnterprise",
])
def test_panels_have_no_raw_pre_dump(html, fn):
    start = html.find("async function " + fn + "(")
    assert start != -1, f"{fn} not found"
    tail = html[start + 20:]
    end = re.search(r"\n(?:async function|function) ", tail)
    body = tail[:end.start()] if end else tail
    assert "<pre" not in body, f"{fn} still renders a raw <pre> dump"
    assert "loadJsonTab" not in body, f"{fn} still uses loadJsonTab"


def test_registry_exposes_cc_tabs(client):
    body = client.get("/api/cc/modules").json()
    mods = body["modules"]
    assert mods, "no CC modules returned from the registry"
    for m in mods:
        assert m["tab"], f"module {m['name']} has no tab"
        assert m["status"] in ("live", "hidden", "retired")
        assert "title" in m


def test_arbitra_registered_with_health(client):
    mods = {m["name"]: m for m in client.get("/api/cc/modules").json()["modules"]}
    assert "arbitra" in mods
    assert mods["arbitra"]["health"] == "/cc/arbitra/arbitra/overview"


def test_second_brain_summary_shape(client):
    r = client.get("/api/second-brain/summary",
                   headers={"X-Kai-User": "cc@kai", "X-Kai-User-Id": "cc"})
    assert r.status_code == 200
    body = r.json()
    for key in ("total_records", "store_count", "stores", "recent"):
        assert key in body
    assert isinstance(body["stores"], dict)


def test_second_brain_summary_requires_identity(client):
    assert client.get("/api/second-brain/summary").status_code == 401
