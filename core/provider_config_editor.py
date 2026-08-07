"""Phase 17U: Provider config editor — operator-set default workers and fallback order.

Stores an override layer in memory/provider_config_overrides.json consulted
by ai_router before the hardcoded ROLE_PROVIDERS defaults. Two override axes:

  * fallback_order — per-role ordered list of providers. When set for a role,
    it replaces the hardcoded ROLE_PROVIDERS order for that role. Absent roles
    fall through to the hardcoded default unchanged.

  * max_concurrent_builds — operator-set concurrency ceiling for the build
    manager (default workers).

Validation at write-time:
  - Every provider named in fallback_order must be registered in core.ai_provider.
  - Warns (does not reject) when every provider in a role's override is
    currently unavailable, since the operator may know credentials are coming.
"""

import json
from pathlib import Path

import core.ai_provider as ai_provider
from core.memory import load, save


OVERRIDES_FILE = "provider_config_overrides.json"
SCHEMA_VERSION = 1


def _default_overrides():
    return {"schema_version": SCHEMA_VERSION, "overrides": {}}


def load_overrides():
    """Return the current overrides dict, creating a default file if none exists."""
    data = load(OVERRIDES_FILE)
    if not data:
        return _default_overrides()
    if isinstance(data, dict) and data.get("schema_version"):
        return data
    return _default_overrides()


def get_fallback_order(role):
    """Return the operator-set fallback order for *role*, or None if not set.

    ai_router._candidates_for() calls this; when None is returned the hardcoded
    ROLE_PROVIDERS default is used unchanged.
    """
    overrides = load_overrides().get("overrides", {})
    fallback = overrides.get("fallback_order", {})
    order = fallback.get(role)
    if order and isinstance(order, list) and len(order) > 0:
        return order
    return None


def get_max_concurrent_builds():
    """Return the operator-set max_concurrent_builds, or None if not set."""
    overrides = load_overrides().get("overrides", {})
    return overrides.get("max_concurrent_builds")


def validate_overrides(overrides):
    """Validate a proposed overrides dict against registered providers.

    Returns (valid, errors, warnings) tuple:
      - valid: True if the overrides pass all hard validation rules,
        False if they should be rejected outright.
      - errors: list of hard validation failures (reject).
      - warnings: list of soft warnings (accept but notify).
    """
    errors = []
    warnings = []

    registered = set(ai_provider.list_providers().keys())
    fallback = overrides.get("fallback_order", {})

    if not isinstance(fallback, dict):
        errors.append("fallback_order must be a dict mapping role names to provider lists")
        return (False, errors, warnings)

    for role, providers in fallback.items():
        if not isinstance(providers, list):
            errors.append(f"fallback_order.{role} must be a list of provider names")
            continue

        if not providers:
            errors.append(f"fallback_order.{role} is empty — must have at least one provider")
            continue

        seen = set()
        for name in providers:
            if not isinstance(name, str):
                errors.append(f"fallback_order.{role}: provider name must be a string, got {type(name).__name__}")
                continue
            if name not in registered:
                errors.append(
                    f"fallback_order.{role}: provider {name!r} is not registered "
                    "(use GET /providers to list registered providers)"
                )
            if name in seen:
                errors.append(f"fallback_order.{role}: duplicate provider {name!r}")
            seen.add(name)

        # Warn if every provider in this role's override is currently
        # unavailable (operator may know credentials are coming).
        all_unavailable = True
        for name in providers:
            info = ai_provider.get_provider(name)
            if info and info.get("available_fn", lambda: False)():
                all_unavailable = False
                break
        if all_unavailable and providers:
            warnings.append(
                f"fallback_order.{role}: every provider is currently marked "
                "unavailable (no credentials configured)"
            )

    # Validate max_concurrent_builds
    max_conc = overrides.get("max_concurrent_builds")
    if max_conc is not None:
        if not isinstance(max_conc, int) or isinstance(max_conc, bool):
            errors.append(f"max_concurrent_builds must be an integer, got {type(max_conc).__name__}")
        elif max_conc < 1:
            errors.append(f"max_concurrent_builds must be >= 1, got {max_conc}")

    valid = len(errors) == 0
    return (valid, errors, warnings)


def save_overrides(overrides):
    """Validate and persist the overrides dict to memory/.

    The supplied *overrides* is deep-merged on top of the existing overrides
    file: fallback_order sub-keys are merged role-by-role, and top-level keys
    (max_concurrent_builds) simply replace. To fully reset, call delete first.

    Returns (success, errors, warnings) tuple. The overrides dict should have
    the shape:
        {"fallback_order": {"role": ["provider", ...]}, "max_concurrent_builds": N}
    """
    valid, errors, warnings = validate_overrides(overrides)
    if not valid:
        return (False, errors, warnings)

    existing = load_overrides().get("overrides", {})

    merged = dict(existing)
    for key, value in overrides.items():
        if key == "fallback_order" and isinstance(value, dict) and "fallback_order" in merged and isinstance(merged["fallback_order"], dict):
            merged_fallback = dict(merged["fallback_order"])
            merged_fallback.update(value)
            merged["fallback_order"] = merged_fallback
        else:
            merged[key] = value

    wrapped = {
        "schema_version": SCHEMA_VERSION,
        "overrides": merged,
    }
    save(OVERRIDES_FILE, wrapped)
    return (True, [], warnings)


def get_full_config():
    """Return the full current overrides with validation context for the
    GET /providers/config endpoint and dashboard panel.

    Includes the current overrides, the hardcoded defaults for reference,
    and any validation warnings for the active overrides.
    """
    overrides = load_overrides().get("overrides", {})
    _, errors, warnings = validate_overrides(overrides)
    return {
        "schema_version": SCHEMA_VERSION,
        "overrides": overrides,
        "validation": {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        },
    }
