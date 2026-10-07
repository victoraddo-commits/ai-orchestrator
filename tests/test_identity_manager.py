"""Digital Identity Manager (core/identity) — TDD suite.

Isolation: every test points AI_ORCHESTRATOR_MEMORY_DIR at a tmp dir so the
registry never touches production memory/. Event bus and audit are spied.
"""

import importlib
import json

import pytest

from core.identity import (
    DigitalIdentity,
    IdentityCreate,
    IdentityUpdate,
    IdentityStatus,
    SecurityProfile,
    IdentityNotFound,
    DuplicateEmail,
    create_identity,
    get_identity,
    list_identities,
    update_identity,
    archive_identity,
    link_email,
    unlink_email,
    link_phone,
    link_domain,
    link_browser_profile,
    resolve_default_identity,
    set_default_identity,
    identity_vault_namespace,
)


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def bus_spy(monkeypatch):
    events = []

    def _publish(topic, payload, *a, **k):
        events.append((topic, payload))
        return 0

    monkeypatch.setattr("core.kai_event_bus.publish", _publish)
    return events


@pytest.fixture
def audit_spy(monkeypatch):
    events = []

    def _audit(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr("core.audit_logger.log_audit_event", _audit)
    return events


def _create(**overrides):
    payload = {"display_name": "Kai Default", "email": "kai@example.com"}
    payload.update(overrides)
    return create_identity(IdentityCreate(**payload))


def test_identity_status_enum_values():
    assert IdentityStatus.active.value == "active"
    assert IdentityStatus.archived.value == "archived"


def test_identity_vault_namespace_shape():
    assert identity_vault_namespace("ident-abc") == "secrets/identities/ident-abc"


def test_create_identity_basic():
    ident = _create()
    assert isinstance(ident, DigitalIdentity)
    assert ident.identity_id.startswith("ident-")
    assert ident.display_name == "Kai Default"
    assert ident.email == "kai@example.com"
    assert "kai@example.com" in ident.emails
    assert ident.status == IdentityStatus.active
    assert ident.vault_namespace == f"secrets/identities/{ident.identity_id}"
    assert ident.created_at and ident.updated_at


def test_create_rejects_invalid_email():
    with pytest.raises(Exception):
        IdentityCreate(display_name="x", email="not-an-email")


def test_create_rejects_secret_fields():
    with pytest.raises(Exception):
        IdentityCreate(display_name="x", email="a@b.com", password="hunter2")


def test_create_rejects_api_key_field():
    with pytest.raises(Exception):
        IdentityCreate(display_name="x", vault_namespace="secrets/x", api_key="sk-xyz")


def test_get_identity_roundtrip_and_missing():
    ident = _create()
    assert get_identity(ident.identity_id).identity_id == ident.identity_id
    assert get_identity("nope") is None


def test_list_identities_search_and_status_filter():
    _create(display_name="Alpha", email="alpha@example.com")
    beta = _create(display_name="Beta", email="beta@example.com")
    assert len(list_identities()) == 2
    assert [i.display_name for i in list_identities(search="beta")] == ["Beta"]
    archive_identity(beta.identity_id)
    assert [i.display_name for i in list_identities(status="archived")] == ["Beta"]


def test_update_identity_fields():
    ident = _create()
    updated = update_identity(
        ident.identity_id,
        IdentityUpdate(display_name="Renamed", security_profile=SecurityProfile.hardened),
    )
    assert updated.display_name == "Renamed"
    assert updated.security_profile == SecurityProfile.hardened


def test_update_missing_raises():
    with pytest.raises(IdentityNotFound):
        update_identity("nope", IdentityUpdate(display_name="x"))


def test_archive_identity():
    ident = _create()
    assert archive_identity(ident.identity_id).status == IdentityStatus.archived
    assert get_identity(ident.identity_id).status == IdentityStatus.archived


def test_link_and_unlink_email():
    ident = _create(email="primary@example.com")
    ident = link_email(ident.identity_id, "second@example.com")
    assert "second@example.com" in ident.emails
    ident = link_email(ident.identity_id, "second@example.com")
    assert ident.emails.count("second@example.com") == 1
    ident = unlink_email(ident.identity_id, "primary@example.com")
    assert ident.email == "second@example.com"
    assert "primary@example.com" not in ident.emails


def test_link_phone_and_domain():
    ident = _create()
    ident = link_phone(ident.identity_id, "+233200000000")
    ident = link_domain(ident.identity_id, "example.com")
    assert ident.phone == "+233200000000"
    assert ident.domain == "example.com"
    assert "example.com" in ident.domains


def test_link_browser_profile():
    ident = _create()
    ident = link_browser_profile(ident.identity_id, "profiles/kai-default")
    assert "profiles/kai-default" in ident.browser_profiles


def test_unique_email_across_identities():
    _create(email="dup@example.com")
    with pytest.raises(DuplicateEmail):
        _create(email="dup@example.com")
    other = _create(email="other@example.com")
    with pytest.raises(DuplicateEmail):
        link_email(other.identity_id, "dup@example.com")


def test_default_identity_resolution():
    a = _create(email="a@example.com")
    b = _create(email="b@example.com")
    assert resolve_default_identity() is None
    set_default_identity(b.identity_id)
    assert resolve_default_identity().identity_id == b.identity_id
    set_default_identity(a.identity_id)
    assert resolve_default_identity().identity_id == a.identity_id
    assert sum(1 for i in list_identities() if i.is_default) == 1


def test_identity_created_event_and_audit(bus_spy, audit_spy):
    _create(email="evt@example.com")
    assert "identity.created" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "identity.create" for e in audit_spy)


def test_identity_updated_event_and_audit(bus_spy, audit_spy):
    ident = _create(email="upd@example.com")
    update_identity(ident.identity_id, IdentityUpdate(display_name="Z"))
    assert "identity.updated" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "identity.update" for e in audit_spy)


def test_store_boundary_rejects_secret_keys():
    from core.identity import store

    with pytest.raises(Exception):
        store.update_records(lambda recs: recs + [{"identity_id": "x", "password": "p"}])


def test_persistence_survives_reload(isolated_memory):
    ident = _create(email="persist@example.com")
    path = isolated_memory / "identity_registry.json"
    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded["schema_version"] == 1
    import core.identity.manager as manager

    importlib.reload(manager)
    assert manager.get_identity(ident.identity_id).email == "persist@example.com"
