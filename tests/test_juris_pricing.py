"""TDD tests for editable Juris Kai pricing.

Covers the config store (``core/juris_kai/pricing.py``), its live application
into ``accounts.py``, and the Command Center GET/PUT endpoints. No network and
no shared production DB — pricing lives in a tmp JSON file and the accounts
dict is restored after every test.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.juris_kai import cc_routes
from core.juris_kai import pricing

BRIDGE = {"Authorization": "Bearer test-bridge-token"}


def _accts():
    """Import accounts lazily so the DB path is resolved after env setup."""
    from core.juris_kai import accounts
    return accounts


@pytest.fixture(autouse=True)
def restore_accounts_pricing():
    """Snapshot/restore the live pricing applied into accounts.py."""
    accts = _accts()
    saved_tiers = deepcopy(accts.SUBSCRIPTION_TIERS)
    saved_rate = accts.PER_DOCUMENT_PAGE_RATE_GHS
    yield
    accts.SUBSCRIPTION_TIERS.clear()
    accts.SUBSCRIPTION_TIERS.update(saved_tiers)
    accts.PER_DOCUMENT_PAGE_RATE_GHS = saved_rate


@pytest.fixture
def pricing_path(tmp_path, monkeypatch):
    path = tmp_path / "juris_pricing.json"
    monkeypatch.setenv("JURIS_PRICING_PATH", str(path))
    return path


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    cc_routes._rate_state.clear()
    app = FastAPI()
    app.include_router(cc_routes.router)
    return TestClient(app)


def _valid_tiers():
    return deepcopy(pricing.DEFAULT_TIERS)


# ── config store ──────────────────────────────────────────────────────────

class TestPricingStore:
    def test_defaults_match_legacy_values(self):
        assert pricing.DEFAULT_TIERS["monthly_basic"]["price_ghs"] == 50
        assert pricing.DEFAULT_TIERS["monthly_pro"]["price_ghs"] == 150
        assert pricing.DEFAULT_TIERS["annual_pro"]["price_ghs"] == 1500
        assert pricing.DEFAULT_PER_DOCUMENT_PAGE_RATE_GHS == 2.0
        assert len(pricing.DEFAULT_TIERS) == 4

    def test_load_without_file_returns_defaults(self, pricing_path):
        doc = pricing.load_pricing()
        assert doc["tiers"] == pricing.DEFAULT_TIERS
        assert doc["per_document_page_rate_ghs"] == 2.0

    def test_save_then_load_roundtrip(self, pricing_path):
        tiers = _valid_tiers()
        tiers["monthly_basic"]["price_ghs"] = 75
        doc = pricing.save_pricing(tiers, 3.5, updated_by="tester")

        assert doc["tiers"]["monthly_basic"]["price_ghs"] == 75
        assert doc["per_document_page_rate_ghs"] == 3.5
        assert doc["updated_by"] == "tester"
        assert json.loads(pricing_path.read_text())["tiers"]["monthly_basic"]["price_ghs"] == 75

        reloaded = pricing.load_pricing()
        assert reloaded["tiers"]["monthly_basic"]["price_ghs"] == 75
        assert reloaded["per_document_page_rate_ghs"] == 3.5

    def test_save_applies_to_accounts_in_place(self, pricing_path):
        tiers = _valid_tiers()
        tiers["monthly_pro"]["price_ghs"] = 200
        pricing.save_pricing(tiers, 4.0)
        accts = _accts()
        assert accts.SUBSCRIPTION_TIERS["monthly_pro"]["price_ghs"] == 200
        assert accts.PER_DOCUMENT_PAGE_RATE_GHS == 4.0

    def test_corrupt_file_falls_back_to_defaults(self, pricing_path):
        pricing_path.write_text("{not json")
        doc = pricing.load_pricing()
        assert doc["tiers"] == pricing.DEFAULT_TIERS

    @pytest.mark.parametrize("mutate,needle", [
        (lambda t: t["monthly_basic"].__setitem__("price_ghs", -1), "price_ghs"),
        (lambda t: t["monthly_basic"].__setitem__("duration_days", 0), "duration_days"),
        (lambda t: t["monthly_basic"].__setitem__("max_queries_per_day", -5), "max_queries_per_day"),
        (lambda t: t["monthly_basic"].__setitem__("max_documents_per_month", -1), "max_documents_per_month"),
        (lambda t: t["monthly_basic"].__setitem__("features", "not-a-list"), "features"),
        (lambda t: t["monthly_basic"].__setitem__("name", ""), "name"),
    ])
    def test_validate_rejects_bad_fields(self, mutate, needle):
        tiers = _valid_tiers()
        mutate(tiers)
        errors = pricing.validate_pricing(tiers, 2.0)
        assert any(needle in e for e in errors)

    def test_validate_rejects_negative_page_rate(self):
        errors = pricing.validate_pricing(_valid_tiers(), -0.5)
        assert any("per_document_page_rate_ghs" in e for e in errors)

    def test_validate_accepts_valid(self):
        assert pricing.validate_pricing(_valid_tiers(), 2.0) == []

    def test_save_rejects_invalid(self, pricing_path):
        tiers = _valid_tiers()
        tiers["monthly_basic"]["price_ghs"] = -1
        with pytest.raises(pricing.PricingValidationError):
            pricing.save_pricing(tiers, 2.0)
        assert not pricing_path.exists()


# ── CC endpoints ──────────────────────────────────────────────────────────

class TestPricingEndpoints:
    def test_get_requires_credentials(self, client):
        assert client.get("/api/juris-kai/cc/pricing").status_code == 401

    def test_get_returns_tiers_and_rate(self, client, pricing_path):
        body = client.get("/api/juris-kai/cc/pricing", headers=BRIDGE).json()
        assert body["success"] is True
        assert set(body["tiers"]) == set(pricing.DEFAULT_TIERS)
        assert body["per_document_page_rate_ghs"] == 2.0
        assert body["defaults"]["per_document_page_rate_ghs"] == 2.0

    def test_put_requires_credentials(self, client):
        r = client.put("/api/juris-kai/cc/pricing", json={"tiers": {}, "per_document_page_rate_ghs": 2})
        assert r.status_code == 401

    def test_put_session_without_capability_forbidden(self, client, monkeypatch):
        monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: False)
        r = client.put("/api/juris-kai/cc/pricing",
                       headers={"X-Kai-Session": "viewer"},
                       json={"tiers": _valid_tiers(), "per_document_page_rate_ghs": 2})
        assert r.status_code == 403

    def test_put_persists_and_audits(self, client, pricing_path, monkeypatch):
        audited = {}
        monkeypatch.setattr(cc_routes, "_log_admin",
                            lambda op, action, meta=None: audited.update(
                                {"op": op, "action": action, "meta": meta}))
        tiers = _valid_tiers()
        tiers["monthly_basic"]["price_ghs"] = 60
        tiers["monthly_basic"]["features"] = ["basic_legal_qa", "case_lookup"]
        r = client.put("/api/juris-kai/cc/pricing", headers=BRIDGE,
                       json={"tiers": tiers, "per_document_page_rate_ghs": 2.5})
        body = r.json()
        assert body["success"] is True
        assert body["tiers"]["monthly_basic"]["price_ghs"] == 60
        assert pricing_path.exists()
        accts = _accts()
        assert accts.SUBSCRIPTION_TIERS["monthly_basic"]["price_ghs"] == 60
        assert accts.PER_DOCUMENT_PAGE_RATE_GHS == 2.5
        assert audited["action"] == "pricing_update"

    def test_put_invalid_returns_validation_errors_and_writes_nothing(self, client, pricing_path):
        tiers = _valid_tiers()
        tiers["monthly_basic"]["price_ghs"] = -3
        r = client.put("/api/juris-kai/cc/pricing", headers=BRIDGE,
                       json={"tiers": tiers, "per_document_page_rate_ghs": 2.0})
        body = r.json()
        assert body["success"] is False
        assert body["validation_errors"]
        assert not pricing_path.exists()

    def test_put_missing_fields(self, client, pricing_path):
        r = client.put("/api/juris-kai/cc/pricing", headers=BRIDGE, json={})
        body = r.json()
        assert body["success"] is False
        assert len(body["validation_errors"]) == 2
