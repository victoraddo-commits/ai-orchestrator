"""Versioned Site Recipe store.

One JSON file per domain under ``memory/site_recipes/<domain>.json`` holding a
``{"domain", "versions": [...]}`` envelope. Writes go through the canonical
atomic primitive ``core.memory.update`` (fcntl.flock + ``.bak`` + ``os.replace``)
with the secret guard at the storage boundary, mirroring
``core.providers.store``.
"""

from __future__ import annotations

import os
import re
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


class RecipeNotFound(KeyError):
    """Raised when a publish/mark target version does not exist in the store."""

    def __init__(self, domain: str, version: Optional[int] = None):
        self.domain = normalize_domain(domain)
        self.version = version
        where = f"version {version}" if version is not None else "recipe"
        super().__init__(f"recipe not found: {self.domain} ({where})")


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


#: A secret-looking *assignment* embedded in a string value, e.g.
#: ``...?token=hunter2`` or ``password: hunter2``. Deliberately narrower than
#: :func:`_looks_secret`: a selector such as ``input[type=password]`` names an
#: attribute, it does not carry a value, so it must not trip the guard.
_SECRET_VALUE_RE = re.compile(
    r"(?:" + "|".join(re.escape(m) for m in SECRET_KEY_MARKERS) + r")\s*[=:]\s*\S+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)


def _looks_secret_value(text) -> bool:
    blob = str(text or "")
    return bool(_SECRET_VALUE_RE.search(blob) or _BEARER_RE.search(blob))


def find_recipe_secret_fields(versions: list[dict]) -> list[str]:
    """Dotted paths of secret-looking material in a persisted recipe payload.

    Extends the house guard (:func:`core.secret_guard.find_secret_fields`,
    which only inspects *keys*) with the recipe-specific rules:

    * a form field whose human ``name`` looks like a credential must carry a
      symbolic ``value_source`` (e.g. ``generated_password``); otherwise the
      recipe is persisting a raw secret slot; and
    * ``selector``/``url``/``description`` must not carry a secret *value*
      (``token=...``, ``password: ...``, ``Bearer ...``). A selector that
      merely points at a password input (``input[type=password]``) is
      metadata and is allowed.
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
            if _looks_secret_value(field.get("selector")):
                found.append(f"versions[{index}].fields[{field_index}].selector")
        for step_index, step in enumerate(version.get("steps", []) or []):
            if not isinstance(step, dict):
                continue
            for key in ("selector", "url", "description"):
                if _looks_secret_value(step.get(key)):
                    found.append(f"versions[{index}].steps[{step_index}].{key}")
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
    target_version: Optional[int] = None

    def _mutate(versions: list[dict]) -> list[dict]:
        nonlocal target_version
        target = version if version is not None else _latest_version(versions)
        # Resolve the target BEFORE any mutation: an unknown version must not
        # demote the currently published row.
        if not any(int(row.get("version", 0)) == target for row in versions):
            raise RecipeNotFound(domain, version=target)
        for row in versions:
            if (row.get("status") == RecipeStatus.published.value
                    and int(row.get("version", 0)) != target):
                row["status"] = RecipeStatus.draft.value
        for row in versions:
            if int(row.get("version", 0)) == target:
                row["status"] = RecipeStatus.published.value
                row["last_verified_at"] = now_iso()
                row["updated_at"] = now_iso()
        target_version = target
        return versions

    _mutate_versions(domain, _mutate)
    return get_recipe(domain, version=target_version)


def mark_stale(domain: str) -> Optional[SiteRecipe]:
    stale_version: Optional[int] = None

    def _mutate(versions: list[dict]) -> list[dict]:
        nonlocal stale_version
        newest = None
        for row in versions:
            if row.get("status") == RecipeStatus.published.value:
                row["status"] = RecipeStatus.stale.value
                row["updated_at"] = now_iso()
                row_version = int(row.get("version", 0))
                if newest is None or row_version > newest:
                    newest = row_version
        stale_version = newest
        return versions

    _mutate_versions(domain, _mutate)
    if stale_version is None:
        return None
    return get_recipe(domain, version=stale_version)


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
