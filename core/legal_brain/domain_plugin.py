"""Legal Brain Domain Plugin Architecture — phase 18F.

Design-only scaffolding for future non-Ghana jurisdictions. Every domain
declares its own DB, vector store, and knowledge graph via a JSON manifest
under ``config/legal_domains/``; the registry loads them, validates against
``domain_plugin.schema.json``, and enforces the "at most N active domains"
invariant (default N=1). Cross-domain queries — when they exist — go through
a mediator, never by opening another domain's DB directly.

Ghana Legal Brain (KLAUS) is the reference implementation manifest
(``config/legal_domains/ghana.json``); Medical / Family / Finance ship as
disabled stubs so the shape is real, not aspirational.

Nothing in this module touches existing legal-brain code — the phase is
design + interface, not integration. Concrete Plugin implementations that
back each manifest live outside this module and are wired up per-domain
later.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Optional

import jsonschema

from core.memory import load as _mem_load, save as _mem_save

logger = logging.getLogger(__name__)

# ── module-level constants ─────────────────────────────────────────────────

CONFIG_DIR = Path("config/legal_domains")
SCHEMA_FILE = CONFIG_DIR / "domain_plugin.schema.json"
STATE_FILE = "legal_domains_active"  # memory/-relative, no .json suffix
DEFAULT_MAX_ACTIVE = 1


# ── plugin interface ───────────────────────────────────────────────────────


class DomainPlugin(ABC):
    """Abstract interface every jurisdiction plugin must implement.

    Instances are constructed from a validated manifest dict; concrete
    subclasses live in per-domain modules and are wired to their own DB /
    vector store / knowledge graph. Never share storage across plugins.
    """

    def __init__(self, manifest: dict[str, Any]) -> None:
        self._manifest = dict(manifest)

    # ── identity ──────────────────────────────────────────────────────────
    def id(self) -> str:
        """Stable kebab-case identifier — matches manifest.id."""
        return self._manifest["id"]

    def name(self) -> str:
        """Human display name."""
        return self._manifest["name"]

    def jurisdiction(self) -> dict[str, str]:
        """``{"country": "GH", "subdivision": null}`` — ISO 3166-1 alpha-2."""
        return self._manifest.get("jurisdiction", {}) or {}

    # ── isolation paths ───────────────────────────────────────────────────
    def db_path(self) -> str:
        """Path to this domain's exclusive SQL database. Never shared."""
        return self._manifest["db_path"]

    def vector_store_path(self) -> str:
        """Path to this domain's exclusive vector store. Never shared."""
        return self._manifest["vector_store_path"]

    def kg_path(self) -> str:
        """Path to this domain's exclusive knowledge graph store."""
        return self._manifest["kg_path"]

    # ── sources ───────────────────────────────────────────────────────────
    def sources(self) -> list[dict[str, Any]]:
        """List of source objects: ``[{"url", "trust_score", "status"}, …]``."""
        return list(self._manifest.get("sources", []))

    # ── lifecycle ─────────────────────────────────────────────────────────
    @abstractmethod
    def activate(self) -> None:
        """Called when the registry activates this domain. Open handles,
        prime caches, verify storage exists."""

    @abstractmethod
    def deactivate(self) -> None:
        """Called when the registry deactivates this domain. Close handles,
        flush caches. Must be idempotent."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return ``{"status": "ok"|"degraded"|"down"|"unknown", "detail": ...}``
        without side effects. Called from ``GET /api/legal/domains/{id}/health``."""

    # ── query surface (stubs for design phase) ────────────────────────────
    @abstractmethod
    def search(self, query: str) -> Iterable[dict[str, Any]]:
        """Return matches for a natural-language query. Each match is a dict
        with at minimum ``id``, ``score``, ``snippet``, ``source_id``."""

    @abstractmethod
    def cite(self, match: dict[str, Any]) -> dict[str, Any]:
        """Turn a match into a formal citation per ``manifest.citation_style``."""


class StubPlugin(DomainPlugin):
    """Concrete no-op implementation used for manifests that don't yet have
    a real plugin class wired up. Every abstract method returns a placeholder
    or raises ``NotImplementedError`` in a controlled way; ``health()`` reports
    ``unknown`` so dashboards can label the domain accurately."""

    def activate(self) -> None:
        logger.info("stub plugin %s activate — no-op", self.id())

    def deactivate(self) -> None:
        logger.info("stub plugin %s deactivate — no-op", self.id())

    def health(self) -> dict[str, Any]:
        return {"status": "unknown", "detail": "stub plugin — no backing implementation"}

    def search(self, query: str) -> Iterable[dict[str, Any]]:
        return []

    def cite(self, match: dict[str, Any]) -> dict[str, Any]:
        return {"citation": "", "style": self._manifest.get("citation_style", "unknown")}


# ── registry ───────────────────────────────────────────────────────────────


class PluginRegistry:
    """Loads plugin manifests from ``config/legal_domains/*.json``, validates
    each against the schema, and tracks which domain(s) are active. Active
    state persists in ``memory/legal_domains_active.json``.

    Concrete DomainPlugin subclasses are registered via ``register_impl()``;
    manifests without a registered class fall back to ``StubPlugin`` so
    dashboards can still surface them.
    """

    def __init__(
        self,
        config_dir: Path | str = CONFIG_DIR,
        max_active: int = DEFAULT_MAX_ACTIVE,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.max_active = max(1, int(max_active))
        self._schema: Optional[dict[str, Any]] = None
        self._manifests: dict[str, dict[str, Any]] = {}
        self._impls: dict[str, type[DomainPlugin]] = {}
        self._instances: dict[str, DomainPlugin] = {}
        self._load_schema()
        self._load_manifests()

    # ── loading ───────────────────────────────────────────────────────────
    def _load_schema(self) -> None:
        schema_path = self.config_dir / "domain_plugin.schema.json"
        if not schema_path.exists():
            raise FileNotFoundError(f"missing schema: {schema_path}")
        with schema_path.open() as f:
            self._schema = json.load(f)

    def _load_manifests(self) -> None:
        """Discover, validate, and cache every manifest in the config dir."""
        if not self.config_dir.exists():
            return
        for path in sorted(self.config_dir.glob("*.json")):
            if path.name == "domain_plugin.schema.json":
                continue
            try:
                with path.open() as f:
                    m = json.load(f)
                jsonschema.validate(m, self._schema)
                self._manifests[m["id"]] = m
            except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                logger.warning("skipping invalid manifest %s: %s", path.name, e)

    # ── registration of concrete implementations ──────────────────────────
    def register_impl(self, domain_id: str, cls: type[DomainPlugin]) -> None:
        """Bind a concrete DomainPlugin subclass to a manifest id."""
        self._impls[domain_id] = cls

    def _instance(self, domain_id: str) -> DomainPlugin:
        if domain_id in self._instances:
            return self._instances[domain_id]
        m = self._manifests[domain_id]
        cls = self._impls.get(domain_id, StubPlugin)
        inst = cls(m)
        self._instances[domain_id] = inst
        return inst

    # ── introspection ─────────────────────────────────────────────────────
    def list_manifests(self) -> list[dict[str, Any]]:
        """Return all loaded manifests, sorted by id."""
        return [dict(self._manifests[k]) for k in sorted(self._manifests)]

    def get_manifest(self, domain_id: str) -> Optional[dict[str, Any]]:
        m = self._manifests.get(domain_id)
        return dict(m) if m else None

    def has(self, domain_id: str) -> bool:
        return domain_id in self._manifests

    # ── active-state persistence ──────────────────────────────────────────
    def _read_state(self) -> dict[str, Any]:
        data = _mem_load(STATE_FILE) or {}
        if isinstance(data, dict) and "active" in data:
            return data
        return {"schema_version": 1, "active": []}

    def _write_state(self, active_ids: list[str]) -> None:
        _mem_save(STATE_FILE, {"schema_version": 1, "active": active_ids})

    def active_ids(self) -> list[str]:
        """List of currently active domain ids (order = activation order)."""
        return list(self._read_state().get("active", []))

    def is_active(self, domain_id: str) -> bool:
        return domain_id in self.active_ids()

    # ── activation ────────────────────────────────────────────────────────
    def activate(self, domain_id: str) -> dict[str, Any]:
        """Activate a domain. If activating would exceed ``max_active``,
        the oldest currently-active domain is deactivated. Returns
        ``{"activated": id, "deactivated": [ids…]}``."""
        if not self.has(domain_id):
            raise KeyError(f"unknown domain: {domain_id}")

        current = self.active_ids()
        deactivated: list[str] = []
        if domain_id in current:
            # No-op — already active; still call plugin.activate() to prime.
            self._instance(domain_id).activate()
            return {"activated": domain_id, "deactivated": []}

        while len(current) + 1 > self.max_active and current:
            oldest = current.pop(0)
            self._instance(oldest).deactivate()
            deactivated.append(oldest)

        current.append(domain_id)
        self._instance(domain_id).activate()
        self._write_state(current)
        return {"activated": domain_id, "deactivated": deactivated}

    def deactivate(self, domain_id: str) -> dict[str, Any]:
        """Deactivate a domain; idempotent."""
        if not self.has(domain_id):
            raise KeyError(f"unknown domain: {domain_id}")
        current = self.active_ids()
        if domain_id not in current:
            return {"deactivated": domain_id, "was_active": False}
        current.remove(domain_id)
        self._instance(domain_id).deactivate()
        self._write_state(current)
        return {"deactivated": domain_id, "was_active": True}

    def health(self, domain_id: str) -> dict[str, Any]:
        """Return the plugin's health verdict. Unloaded/unknown domains
        report ``{"status": "unknown"}`` per the phase-18F contract."""
        if not self.has(domain_id):
            return {"status": "unknown", "detail": "no manifest"}
        return self._instance(domain_id).health()


# Singleton — lazy so tests can construct their own with a temp config dir.
_registry: Optional[PluginRegistry] = None


def get_registry() -> PluginRegistry:
    """Return the module-level singleton registry."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def reset_registry() -> None:
    """Test-only: clear the singleton so a fresh instance is built."""
    global _registry
    _registry = None
