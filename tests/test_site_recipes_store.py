"""core.site_recipes.store - versioned CRUD, promotion, stale, no secrets."""

import pytest

from core.secret_guard import SecretFieldError
from core.site_recipes import store
from core.site_recipes.schema import (
    FieldSpec,
    RecipeStatus,
    SiteRecipe,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


def _recipe(domain="example.com", **kw):
    return SiteRecipe(domain=domain, **kw)


def test_save_assigns_incrementing_versions(isolated):
    first = store.save_recipe(_recipe())
    second = store.save_recipe(_recipe())
    assert first.version == 1
    assert second.version == 2
    assert store.get_recipe("example.com").version == 2


def test_recipe_file_is_persisted_under_site_recipes(isolated, tmp_path):
    store.save_recipe(_recipe())
    path = tmp_path / "site_recipes" / "example.com.json"
    assert path.exists()
    assert '"versions"' in path.read_text()


def test_publish_promotes_latest_and_is_fetchable(isolated):
    store.save_recipe(_recipe())
    store.save_recipe(_recipe())
    published = store.publish_recipe("example.com")
    assert published.status is RecipeStatus.published
    assert published.version == 2
    assert store.get_published("example.com").version == 2
    assert published.last_verified_at is not None


def test_publish_demotes_an_older_published_version(isolated):
    store.save_recipe(_recipe())
    store.publish_recipe("example.com")          # v1 published
    store.save_recipe(_recipe())
    store.publish_recipe("example.com")          # v2 published
    v1 = store.get_recipe("example.com", version=1)
    assert v1.status is RecipeStatus.draft


def test_mark_stale_flips_published(isolated):
    store.save_recipe(_recipe())
    store.publish_recipe("example.com")
    stale = store.mark_stale("example.com")
    assert stale.status is RecipeStatus.stale
    assert store.get_published("example.com") is None
    assert len(store.list_stale()) == 1


def test_list_recipes_returns_latest_per_domain(isolated):
    store.save_recipe(_recipe("a.example"))
    store.save_recipe(_recipe("a.example"))
    store.save_recipe(_recipe("b.example"))
    domains = sorted(r.domain for r in store.list_recipes())
    assert domains == ["a.example", "b.example"]


def test_store_rejects_secret_looking_recipe_keys(isolated):
    # A recipe carrying a secret-looking field name must be rejected at storage.
    recipe = _recipe(fields=[FieldSpec(name="api_key", selector="#k")])
    with pytest.raises(SecretFieldError):
        store.save_recipe(recipe)
