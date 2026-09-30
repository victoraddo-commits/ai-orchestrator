"""Versioned Site Recipe store.

One JSON file per domain under ``memory/site_recipes/<domain>.json`` holding a
``{"domain", "versions": [...]}`` envelope. Writes go through the canonical
atomic primitive ``core.memory.update`` (fcntl.flock + ``.bak`` + ``os.replace``)
with the secret guard at the storage boundary, mirroring
``core.providers.store``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from core.memory import load, update
from core.secret_guard import (
    SECRET_KEY_MARKERS,
    SecretFieldError,
    assert_no_secret_fields,
    find_secret_fields,
)

from core.site_recipes.schema import (
    RecipeStatus,
    SiteRecipe,
    normalize_domain,
    now_iso,
)


def memory_dir() -> Path:
    """Memory directory resolved at call time so tests can isolate per-run."""
    return Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))


def recipe_name(domain: str) -> str:
    return f"site_recipes/{normalize_domain(domain)}.json"


def _records(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("versions", [])
    return list(data or [])


def _looks_secret(text) -> bool:
    low = str(text or "").lower()
    return any(marker in low for marker in SECRET_KEY_MARKERS)


def find_recipe_secret_fields(versions: list[dict]) -> list[str]:
    """Dotted paths of secret-looking material in a persisted recipe payload.

    Extends the house guard (:func:`core.secret_guard.find_secret_fields`,
    which only inspects *keys*) with the recipe-specific rule: a form field
    whose human ``name`` looks like a credential must carry a symbolic
    ``value_source`` (e.g. ``generated_password``); otherwise the recipe is
    attempting to persist a raw secret slot.
    """
    found = list(find_secret_fields(versions))
    for index, version in enumerate(versions):
        if not isinstance(version, dict):
            continue
        for field_index, field in enumerate(version.get("fields", []) or []):
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            if _looks_secret(name) and not field.get("value_source"):
                found.append(f"versions[{index}].fields[{field_index}].name={name!r}")
    return found


def assert_no_secret_recipe_fields(versions: list[dict]) -> None:
    """Raise :class:`SecretFieldError` if a recipe payload holds secrets."""
    found = find_recipe_secret_fields(versions)
    if found:
        raise SecretFieldError(
            "refusing to persist secret-looking fields: " + ", ".join(found)
        )


def read_versions(domain: str) -> list[dict]:
    return _records(load(recipe_name(domain), directory=memory_dir()))


def _mutate_versions(domain: str, mutate_fn: Callable[[list[dict]], list[dict]]):
    (memory_dir() / "site_recipes").mkdir(parents=True, exist_ok=True)

    def _mutate(data):
        versions = mutate_fn(_records(data))
        assert_no_secret_recipe_fields(versions)
        return {"domain": normalize_domain(domain), "versions": versions}

    return update(recipe_name(domain), _mutate, directory=memory_dir())


def _latest_version(versions: list[dict]) -> int:
    return max((int(v.get("version", 0)) for v in versions), default=0)


def save_recipe(recipe: SiteRecipe) -> SiteRecipe:
    domain = recipe.domain

    def _mutate(versions: list[dict]) -> list[dict]:
        stored = recipe.model_copy(update={
            "version": _latest_version(versions) + 1,
            "updated_at": now_iso(),
        })
        versions.append(stored.model_dump(mode="json"))
        return versions

    _mutate_versions(domain, _mutate)
    return get_recipe(domain)


def get_recipe(domain: str, *, status=None,
               version: Optional[int] = None) -> Optional[SiteRecipe]:
    versions = read_versions(domain)
    if not versions:
        return None
    if version is not None:
        for row in versions:
            if int(row.get("version", 0)) == version:
                return SiteRecipe(**row)
        return None
    if status is not None:
        target = RecipeStatus(status).value
        matches = [row for row in versions if row.get("status") == target]
        if not matches:
            return None
        return SiteRecipe(**max(matches, key=lambda r: int(r.get("version", 0))))
    return SiteRecipe(**max(versions, key=lambda r: int(r.get("version", 0))))


def get_published(domain: str) -> Optional[SiteRecipe]:
    return get_recipe(domain, status=RecipeStatus.published)


def publish_recipe(domain: str, *,
                   version: Optional[int] = None) -> Optional[SiteRecipe]:
    def _mutate(versions: list[dict]) -> list[dict]:
        target = version or _latest_version(versions)
        for row in versions:
            if (row.get("status") == RecipeStatus.published.value
                    and int(row.get("version", 0)) != target):
                row["status"] = RecipeStatus.draft.value
        for row in versions:
            if int(row.get("version", 0)) == target:
                row["status"] = RecipeStatus.published.value
                row["last_verified_at"] = now_iso()
                row["updated_at"] = now_iso()
        return versions

    _mutate_versions(domain, _mutate)
    return get_published(domain)


def mark_stale(domain: str) -> Optional[SiteRecipe]:
    def _mutate(versions: list[dict]) -> list[dict]:
        for row in versions:
            if row.get("status") == RecipeStatus.published.value:
                row["status"] = RecipeStatus.stale.value
                row["updated_at"] = now_iso()
        return versions

    _mutate_versions(domain, _mutate)
    return get_recipe(domain)


def list_recipes() -> list[SiteRecipe]:
    base = memory_dir() / "site_recipes"
    if not base.exists():
        return []
    out: list[SiteRecipe] = []
    for path in sorted(base.glob("*.json")):
        recipe = get_recipe(path.stem)
        if recipe is not None:
            out.append(recipe)
    return out


def list_stale() -> list[SiteRecipe]:
    return [r for r in list_recipes() if r.status is RecipeStatus.stale]
