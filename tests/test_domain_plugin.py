"""Tests for phase 18F — Legal Brain Domain Plugin Architecture."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.legal_brain.domain_plugin import (
    CONFIG_DIR,
    DomainPlugin,
    PluginRegistry,
    StubPlugin,
    reset_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirect core.memory writes to a per-test dir so registry state
    doesn't leak between tests or into the live memory/ dir. The load/save
    helpers honor a per-call `directory=` arg; monkeypatch the module's
    MEMORY_DIR + default so both paths land in tmp_path."""
    import core.memory as _mem
    monkeypatch.setattr(_mem, "MEMORY_DIR", tmp_path)
    reset_registry()
    yield
    reset_registry()


def _real_config_dir() -> Path:
    """Absolute path to the repo's config/legal_domains/."""
    return REPO_ROOT / "config" / "legal_domains"


# ── manifest / schema tests ────────────────────────────────────────────────


def test_all_four_manifests_validate_against_schema():
    """Every shipped manifest must validate against the JSON schema."""
    schema = json.loads((_real_config_dir() / "domain_plugin.schema.json").read_text())
    for name in ("ghana.json", "medical.json", "family.json", "finance.json"):
        m = json.loads((_real_config_dir() / name).read_text())
        jsonschema.validate(m, schema)


def test_registry_loads_all_four_manifests():
    reg = PluginRegistry(config_dir=_real_config_dir())
    ids = {m["id"] for m in reg.list_manifests()}
    assert {"ghana", "medical", "family", "finance"}.issubset(ids)


def test_registry_skips_bad_manifest(tmp_path):
    """A manifest missing required fields must not crash the registry —
    it's dropped with a warning and the rest still load."""
    (tmp_path / "domain_plugin.schema.json").write_text(
        (_real_config_dir() / "domain_plugin.schema.json").read_text()
    )
    # Copy one valid manifest, add one broken one.
    (tmp_path / "ghana.json").write_text(
        (_real_config_dir() / "ghana.json").read_text()
    )
    (tmp_path / "broken.json").write_text('{"id": "broken"}')  # missing required fields

    reg = PluginRegistry(config_dir=tmp_path)
    ids = {m["id"] for m in reg.list_manifests()}
    assert "ghana" in ids
    assert "broken" not in ids


def test_get_manifest_returns_copy():
    """get_manifest must not leak internal state — callers can mutate freely."""
    reg = PluginRegistry(config_dir=_real_config_dir())
    m = reg.get_manifest("ghana")
    assert m is not None
    m["id"] = "hacked"
    # Second call still returns the pristine manifest.
    assert reg.get_manifest("ghana")["id"] == "ghana"


# ── active-state tests ────────────────────────────────────────────────────


def test_activate_marks_one_active_and_deactivates_others():
    """Activating a second domain with max_active=1 deactivates the first."""
    reg = PluginRegistry(config_dir=_real_config_dir(), max_active=1)
    r1 = reg.activate("ghana")
    assert r1["activated"] == "ghana"
    assert r1["deactivated"] == []
    assert reg.active_ids() == ["ghana"]

    r2 = reg.activate("medical")
    assert r2["activated"] == "medical"
    assert "ghana" in r2["deactivated"]
    assert reg.active_ids() == ["medical"]


def test_max_active_greater_than_one():
    """A registry with max_active=2 allows two concurrent domains."""
    reg = PluginRegistry(config_dir=_real_config_dir(), max_active=2)
    reg.activate("ghana")
    reg.activate("medical")
    assert set(reg.active_ids()) == {"ghana", "medical"}

    # Activating a third evicts the oldest.
    reg.activate("family")
    assert set(reg.active_ids()) == {"medical", "family"}


def test_deactivate_is_idempotent():
    reg = PluginRegistry(config_dir=_real_config_dir())
    reg.activate("ghana")
    r1 = reg.deactivate("ghana")
    assert r1["was_active"] is True
    r2 = reg.deactivate("ghana")
    assert r2["was_active"] is False
    assert reg.active_ids() == []


def test_activate_unknown_raises():
    reg = PluginRegistry(config_dir=_real_config_dir())
    with pytest.raises(KeyError):
        reg.activate("no-such-domain")


# ── health tests ──────────────────────────────────────────────────────────


def test_health_unknown_for_unloaded_domain():
    """An unknown domain reports status=unknown, not a crash."""
    reg = PluginRegistry(config_dir=_real_config_dir())
    verdict = reg.health("no-such-domain")
    assert verdict["status"] == "unknown"


def test_stub_plugin_health_is_unknown():
    """A manifest without a registered impl falls back to StubPlugin which
    always reports 'unknown' — the 18F contract for aspirational stubs."""
    reg = PluginRegistry(config_dir=_real_config_dir())
    verdict = reg.health("medical")
    assert verdict["status"] == "unknown"
    assert "stub" in verdict["detail"].lower()


def test_register_impl_overrides_stub():
    """A concrete DomainPlugin subclass overrides the StubPlugin fallback."""

    class FakeImpl(DomainPlugin):
        def activate(self): pass
        def deactivate(self): pass
        def health(self): return {"status": "ok", "detail": "fake"}
        def search(self, query): return [{"id": "1", "score": 1.0}]
        def cite(self, match): return {"citation": "Fake v Real"}

    reg = PluginRegistry(config_dir=_real_config_dir())
    reg.register_impl("ghana", FakeImpl)
    verdict = reg.health("ghana")
    assert verdict == {"status": "ok", "detail": "fake"}
