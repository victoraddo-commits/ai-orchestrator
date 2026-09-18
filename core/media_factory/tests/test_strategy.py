from core.media_factory import strategy


def test_next_version_from_empty():
    assert strategy.next_version([]) == 1


def test_next_version_increments_highest():
    assert strategy.next_version([1, 2, 5]) == 6


def test_create_version_is_immutable_new_version(monkeypatch):
    monkeypatch.setattr(strategy.db, "query", lambda *a, **k: [{"version": 1}, {"version": 2}])
    monkeypatch.setattr(strategy.db, "insert_returning",
                        lambda sql, params=(): {"id": 10, "version": 3, "active": False})
    monkeypatch.setattr(strategy.db, "audit", lambda *a, **k: {"ok": True})
    row = strategy.create_version({"target": "shorts"}, evidence={"reason": "test"})
    assert row["version"] == 3
    assert row["active"] is False


def test_create_version_without_evidence_is_unverified(monkeypatch):
    captured = {}
    monkeypatch.setattr(strategy.db, "query", lambda *a, **k: [])
    monkeypatch.setattr(strategy.db, "insert_returning",
                        lambda sql, params=(): captured.update(params=params) or
                        {"id": 1, "version": 1})
    monkeypatch.setattr(strategy.db, "audit", lambda *a, **k: {"ok": True})
    strategy.create_version({})
    assert captured["params"][4] == "UNVERIFIED"


def test_activate_missing_version_is_missing(monkeypatch):
    monkeypatch.setattr(strategy.db, "query_one", lambda *a, **k: None)
    result = strategy.activate(99)
    assert result["status"] == "MISSING"
