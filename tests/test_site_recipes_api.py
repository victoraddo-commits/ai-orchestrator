"""CC site-recipe viewer API - auth, shaping, publish, leak-prevention.

Integration tests for the site-recipe routes in ``core/cc_account_routes.py``:
the operator auth gate (401/403), list/detail shapes, publish promotion,
publish validation (unknown domain / unknown version never demotes the
published row), and the hard rule that **no recipe response may carry secret
material** - even when a secret is forced into the store by hand.
"""

import json

import pytest
from fastapi.testclient import TestClient

OP = {"X-Kai-User": "cc@kai", "X-Kai-User-Id": "cc"}
SECRET = "hunter2-not-real"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    import core.notify.human_action as ha

    monkeypatch.setattr(ha, "_send_telegram", lambda text: None)
    return tmp_path


@pytest.fixture
def client():
    from core.api import app

    return TestClient(app)


def _seed_recipe(domain="api.example", *, published=True, password=False):
    from core.site_recipes import store
    from core.site_recipes.schema import (
        FieldSpec,
        RecipeStatus,
        RecipeStep,
        SiteRecipe,
        SiteRequirements,
    )

    fields = []
    if password:
        fields.append(FieldSpec(
            name="password", selector="input[type=password]",
            kind="password", value_source="generated_password", step=1))
    recipe = SiteRecipe(
        domain=domain, signup_url=f"https://{domain}/signup",
        status=RecipeStatus.draft, requirements=SiteRequirements(email=True),
        fields=fields,
        steps=[
            RecipeStep(index=0, action="navigate", url=f"https://{domain}/signup"),
            RecipeStep(index=1, action="fill", selector="#email",
                       value_source="identity.email"),
        ])
    store.save_recipe(recipe)
    if published:
        store.publish_recipe(domain)
    return domain


# ---------------------------------------------------------------------------
# auth gate
# ---------------------------------------------------------------------------


def test_site_recipes_require_operator(client):
    assert client.get("/api/site-recipes").status_code == 401
    assert client.get("/api/site-recipes/api.example").status_code == 401
    assert client.post("/api/site-recipes/api.example/publish").status_code == 401


def test_site_recipes_non_operator_forbidden(client, monkeypatch):
    import core.authz

    monkeypatch.setattr(core.authz, "resolve_role", lambda tok: "viewer")
    headers = {"X-Kai-Session": "not-an-operator-session"}
    assert client.get("/api/site-recipes", headers=headers).status_code == 403
    assert client.get("/api/site-recipes/api.example", headers=headers).status_code == 403
    assert client.post("/api/site-recipes/api.example/publish",
                       headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# list + detail
# ---------------------------------------------------------------------------


def test_list_and_detail(client):
    _seed_recipe("api.example")
    listing = client.get("/api/site-recipes", headers=OP)
    assert listing.status_code == 200
    body = listing.json()
    assert body["ok"] is True and body["count"] >= 1
    row = next(r for r in body["recipes"] if r["domain"] == "api.example")
    assert row["status"] == "published"
    assert row["requirements"]["email"] is True
    assert row["flow_type"] == "unknown"
    assert row["field_count"] == 0 and row["step_count"] == 2

    detail = client.get("/api/site-recipes/api.example", headers=OP)
    assert detail.status_code == 200
    recipe = detail.json()["recipe"]
    assert recipe["steps"][1]["selector"] == "#email"
    assert recipe["steps"][1]["value_source"] == "identity.email"


def test_detail_404_for_unknown(client):
    assert client.get("/api/site-recipes/nope.example", headers=OP).status_code == 404


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def test_publish_promotes_draft(client):
    _seed_recipe("draft.example", published=False)
    r = client.post("/api/site-recipes/draft.example/publish", headers=OP)
    assert r.status_code == 200
    assert r.json()["recipe"]["status"] == "published"


def test_publish_404_for_unknown(client):
    assert client.post("/api/site-recipes/nope.example/publish",
                       headers=OP).status_code == 404


def test_publish_rejects_bad_body(client):
    _seed_recipe("draft2.example", published=False)
    # version must be >= 1 (422), and unknown keys are forbidden (422).
    assert client.post("/api/site-recipes/draft2.example/publish",
                       json={"version": 0}, headers=OP).status_code == 422
    assert client.post("/api/site-recipes/draft2.example/publish",
                       json={"nope": 1}, headers=OP).status_code == 422


def test_publish_unknown_version_404_never_demotes(client):
    from core.site_recipes import store

    _seed_recipe("keep.example", published=True)
    r = client.post("/api/site-recipes/keep.example/publish",
                    json={"version": 99}, headers=OP)
    assert r.status_code == 404
    # The fixed store behaviour: a bad target version must not demote the
    # currently published row.
    current = store.get_recipe("keep.example")
    assert current is not None
    assert current.status.value == "published"
    assert current.version == 1


# ---------------------------------------------------------------------------
# leak-prevention
# ---------------------------------------------------------------------------


def test_password_recipe_is_symbolic_only(client):
    """A recipe naming a password slot is exposed with a symbolic source only."""
    _seed_recipe("secure.example", password=True)
    detail = client.get("/api/site-recipes/secure.example", headers=OP)
    assert detail.status_code == 200
    body = detail.json()
    field = next(f for f in body["recipe"]["fields"] if f["name"] == "password")
    assert field["value_source"] == "generated_password"
    assert SECRET not in detail.text
    from core.secret_guard import find_secret_fields

    assert find_secret_fields(body) == []


def test_no_secret_leak_across_recipe_endpoints(client):
    _seed_recipe("leak.example", password=True)
    calls = [
        ("get", "/api/site-recipes"),
        ("get", "/api/site-recipes/leak.example"),
    ]
    for method, path in calls:
        resp = getattr(client, method)(path, headers=OP)
        assert resp.status_code == 200
        assert SECRET not in resp.text
        data = resp.json()
        from core.secret_guard import find_secret_fields

        assert find_secret_fields(data) == [], f"secret-looking fields in {path}"


def test_literal_secret_forced_into_store_is_never_exposed(client, isolated):
    """A secret written past the model edge must not leave the API.

    The schema and store guard reject secrets normally; if a raw value reaches
    the store file, the response path's secret guard must refuse to shape it
    rather than echo it.
    """
    base = isolated / "site_recipes"
    base.mkdir(parents=True, exist_ok=True)
    (base / "leaky.example.json").write_text(json.dumps({
        "domain": "leaky.example",
        "versions": [{
            "domain": "leaky.example",
            "flow_type": "unknown", "requirements": {"email": True},
            "fields": [], "status": "published", "version": 1,
            "steps": [{"index": 0, "action": "fill",
                       "selector": "input[type=password]",
                       "description": f"password: {SECRET}"}],
        }],
    }))

    listing = client.get("/api/site-recipes", headers=OP)
    assert SECRET not in listing.text

    detail = client.get("/api/site-recipes/leaky.example", headers=OP)
    assert SECRET not in detail.text


def test_schema_rejects_literal_value_source():
    """A literal value_source can never even be constructed (model edge)."""
    from pydantic import ValidationError

    from core.site_recipes.schema import RecipeStep

    with pytest.raises(ValidationError):
        RecipeStep(index=0, action="fill", selector="#p",
                   value_source="hunter2-not-real")


def test_store_guard_rejects_raw_secret_payload():
    from core.secret_guard import SecretFieldError
    from core.site_recipes.store import assert_no_secret_recipe_fields

    with pytest.raises(SecretFieldError):
        assert_no_secret_recipe_fields([{
            "fields": [{"name": "password", "selector": "#p"}],
        }])
