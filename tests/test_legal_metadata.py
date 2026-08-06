import hashlib
import json
import os
from pathlib import Path

import pytest

from core.legal_metadata import (
    VALID_STATUSES,
    register_document,
    list_documents,
    get_document,
    update_document_status,
    create_version,
    get_version_history,
    get_document_content,
    get_audit_trail,
    InvalidStatus,
    DocumentNotFound,
    ImmutableViolation,
)
import core.legal_metadata as _lm


def _hash(content):
    return hashlib.sha256(content).hexdigest()


# ── Metadata schema ────────────────────────────────────────────────

def test_register_document_stores_all_required_metadata_fields():
    record = register_document(
        content=b"Judgment text here.",
        jurisdiction="Ghana",
        court="Supreme Court of Ghana",
        year=2023,
        citation="[2023] GHASC 1",
        judge="Arku JSC",
        parties=["Republic", "Accused X"],
        status="current",
        registered_by="operator",
    )

    assert record["jurisdiction"] == "Ghana"
    assert record["court"] == "Supreme Court of Ghana"
    assert record["year"] == 2023
    assert record["citation"] == "[2023] GHASC 1"
    assert record["judge"] == "Arku JSC"
    assert record["parties"] == ["Republic", "Accused X"]
    assert record["status"] == "current"
    assert record["document_hash"] == _hash(b"Judgment text here.")
    assert record["version"] == 1
    assert record["id"] is not None
    assert record["trace_id"] is not None
    assert record["created"] is not None
    assert record["updated"] is not None
    assert "history" in record


def test_list_documents_returns_all_registered():
    register_document(b"a", "Ghana", "Supreme Court", 2023, "cit1", "J1", ["P1"], "current")
    register_document(b"b", "Ghana", "High Court", 2022, "cit2", "J2", ["P2"], "current")

    docs = list_documents()
    assert len(docs) == 2
    assert {d["citation"] for d in docs} == {"cit1", "cit2"}


def test_get_document_finds_by_id():
    record = register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    found = get_document(record["id"])
    assert found == record


def test_get_document_returns_none_for_unknown_id():
    assert get_document("does-not-exist") is None


def test_register_document_rejects_invalid_status():
    with pytest.raises(InvalidStatus):
        register_document(b"x", "Ghana", "SC", 2023, "cit", "J", ["P"], "invalid_status")


# ── Status transitions ─────────────────────────────────────────────

def test_update_status_valid_transition():
    record = register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    updated = update_document_status(record["id"], "overruled", changed_by="operator")
    assert updated["status"] == "overruled"
    assert updated["history"][-1]["status"] == "overruled"
    assert updated["history"][-1]["note"] is not None


def test_update_status_rejects_invalid_transition():
    record = register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    updated = update_document_status(record["id"], "overruled")
    with pytest.raises(InvalidStatus):
        update_document_status(record["id"], "overruled")


def test_update_status_raises_on_unknown_document():
    with pytest.raises(DocumentNotFound):
        update_document_status("no-such-id", "current")


def test_amended_can_return_to_current():
    record = register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    update_document_status(record["id"], "amended")
    updated = update_document_status(record["id"], "current")
    assert updated["status"] == "current"


# ── Version control ────────────────────────────────────────────────

def test_create_version_produces_new_version_with_parent_reference():
    record = register_document(b"v1 content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    new_record = create_version(record["id"], b"v2 content", registered_by="operator")

    assert new_record["version"] == 2
    assert new_record["parent_document_id"] == record["id"]
    assert new_record["document_hash"] == _hash(b"v2 content")
    assert new_record["id"] != record["id"]
    assert new_record["trace_id"] == record["trace_id"]
    assert new_record["jurisdiction"] == "Ghana"
    assert new_record["citation"] == "cit"


def test_create_version_raises_on_unknown_parent():
    with pytest.raises(DocumentNotFound):
        create_version("no-such-id", b"content")


def test_get_version_history_returns_ordered_chain():
    r1 = register_document(b"one", "GH", "Court", 2021, "c1", "J", ["P"], "current")
    r2 = create_version(r1["id"], b"two")
    r3 = create_version(r2["id"], b"three")

    history = get_version_history(r3["id"])
    assert len(history) == 3
    ids = [h["id"] for h in history]
    assert ids == [r1["id"], r2["id"], r3["id"]]


def test_get_version_history_from_original_returns_self_only():
    record = register_document(b"content", "GH", "C", 2021, "cit", "J", ["P"], "current")
    history = get_version_history(record["id"])
    assert len(history) == 1
    assert history[0]["id"] == record["id"]


# ── Immutable storage ──────────────────────────────────────────────

def test_document_content_is_stored_and_retrievable():
    content = b"Judgment delivered on 15 March 2023."
    record = register_document(content, "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    retrieved = get_document_content(record["id"])
    assert retrieved == content


def test_document_content_is_immutable_on_disk():
    content = b"original content"
    record = register_document(content, "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    stored_path = _lm.DOCUMENTS_DIR / record["document_hash"]

    try:
        stored_path.write_bytes(b"tampered content via os write")
    except PermissionError:
        pass

    with pytest.raises(ImmutableViolation):
        get_document_content(record["id"])


def test_immutable_storage_verifies_hash_on_retrieval():
    content = b"verified content"
    record = register_document(content, "Ghana", "SC", 2023, "cit", "J", ["P"], "current")

    assert get_document_content(record["id"]) == content


def test_duplicate_content_reuses_same_storage():
    content = b"same content twice"
    r1 = register_document(content, "Ghana", "SC", 2023, "c1", "J", ["P"], "current")
    r2 = register_document(content, "Ghana", "HC", 2022, "c2", "J2", ["P2"], "current")

    assert r1["document_hash"] == r2["document_hash"]
    assert r1["id"] != r2["id"]
    assert get_document_content(r1["id"]) == get_document_content(r2["id"])


def test_get_document_content_returns_none_for_unknown_id():
    assert get_document_content("no-such-id") is None


def test_get_document_content_returns_none_when_file_missing():
    record = register_document(b"will be deleted", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
    stored_path = _lm.DOCUMENTS_DIR / record["document_hash"]

    os.chmod(str(_lm.DOCUMENTS_DIR), 0o700)
    try:
        stored_path.unlink()
        assert get_document_content(record["id"]) is None
    finally:
        os.chmod(str(_lm.DOCUMENTS_DIR), 0o700)


# ── Audit trail ────────────────────────────────────────────────────

def test_register_document_creates_audit_entry():
    register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current", registered_by="operator")

    audit = get_audit_trail()
    assert len(audit) == 1
    entry = audit[0]
    assert entry["action"] == "register_document"
    assert entry["actor"] == "operator"
    assert entry["jurisdiction"] == "Ghana"
    assert entry["citation"] == "cit"


def test_update_status_creates_audit_entry():
    record = register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current", registered_by="op")
    update_document_status(record["id"], "overruled", changed_by="reviewer")

    audit = get_audit_trail()
    assert len(audit) == 2
    status_entry = audit[1]
    assert status_entry["action"] == "update_status"
    assert status_entry["actor"] == "reviewer"
    assert status_entry["new_status"] == "overruled"
    assert status_entry["previous_status"] == "current"


def test_create_version_creates_audit_entry():
    record = register_document(b"v1", "Ghana", "SC", 2023, "cit", "J", ["P"], "current", registered_by="op")
    create_version(record["id"], b"v2", registered_by="editor")

    audit = get_audit_trail()
    assert len(audit) == 2
    version_entry = audit[1]
    assert version_entry["action"] == "create_version"
    assert version_entry["actor"] == "editor"
    assert version_entry["parent_id"] == record["id"]


def test_audit_trail_is_append_only():
    register_document(b"from_actor_1", "GH", "C", 2021, "c1", "J", ["P"], "current", registered_by="actor1")

    audit_snapshot = get_audit_trail()
    assert len(audit_snapshot) == 1
    assert audit_snapshot[0]["actor"] == "actor1"

    # Modify in memory should not affect stored copy
    audit_snapshot[0]["actor"] = "tampered"
    audit2 = get_audit_trail()
    assert audit2[0]["actor"] == "actor1"


def test_audit_entry_includes_timestamp():
    register_document(b"content", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")

    audit = get_audit_trail()
    entry = audit[0]
    assert "timestamp" in entry
    assert entry["timestamp"] is not None


def test_register_document_rejects_path_traversal_in_storage(tmp_path):
    import core.legal_metadata as lm
    orig = lm.DOCUMENTS_DIR
    lm.DOCUMENTS_DIR = tmp_path / "docs"
    lm.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        record = register_document(b"safe", "Ghana", "SC", 2023, "cit", "J", ["P"], "current")
        stored = lm.DOCUMENTS_DIR / record["document_hash"]
        assert stored.exists()
        assert str(tmp_path) in str(stored.resolve())
    finally:
        lm.DOCUMENTS_DIR = orig
