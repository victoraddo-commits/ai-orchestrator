# 18F: Legal Brain Domain Plugin Architecture

**Status:** Design spec (implementation deferred to later phases)  
**Created:** 2026-08-07  
**Module:** legal_brain  
**Priority:** 20  
**Dependency:** 18C (Zero-Trust Legal Brain Architecture)

## 1. Motivation

The Legal Brain currently hardcodes Ghana as the sole jurisdiction. `legal_brain/config.py`
declares `ALLOWED_JURISDICTIONS = frozenset({"Ghana"})`, and every module from metadata
(`CourtLevel`, `LegislationType`, `GhanaLegalCitation`) to QC agents (`KNOWN_GHANA_LEGAL_SOURCES`)
is jurisdiction-locked.

This design defines a plugin architecture that makes jurisdiction-specific code pluggable,
with Ghana as the reference implementation. The same architecture extends to non-legal
domains (Medical, Family, Finance) with the same isolation and activation model.

### Design goals

1. **Isolation** — each domain/jurisdiction gets its own database, vector store, and
   knowledge graph. A corrupted Kenya DB must never affect Ghana.
2. **Activation model** — domains are disabled by default. The operator activates them
   explicitly via the Command Center or API.
3. **Ghana-first** — the existing Ghana implementation ships as the reference plugin.
   New jurisdictions copy its structure.
4. **Extensible enums** — court hierarchies, legislation types, citation formats vary
   by jurisdiction. Plugins declare these, and the core adapts.
5. **Minimal core changes** — the plugin system is additive. No existing Ghana
   functionality is broken during migration.

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Legal Brain Core                          │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Plugin       │  │ Storage       │  │ QC Orchestrator   │  │
│  │ Registry     │  │ Router        │  │ (Agent Dispatcher)│  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬──────────┘  │
│         │                  │                    │             │
│         ▼                  ▼                    ▼             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Plugin Interface (ABC)                    │   │
│  │  - get_jurisdiction_metadata()                        │   │
│  │  - get_court_hierarchy()                              │   │
│  │  - get_legislation_types()                            │   │
│  │  - get_citation_parser()                              │   │
│  │  - get_qc_agents()                                    │   │
│  │  - get_storage_config()                               │   │
│  │  - get_knowledge_sources()                            │   │
│  │  - get_taxonomy()                                     │   │
│  └──────┬───────┬───────┬───────┬────────────────────────┘   │
│         │       │       │       │                              │
└─────────┼───────┼───────┼───────┼──────────────────────────────┘
          │       │       │       │
    ┌─────▼──┐ ┌──▼───┐ ┌─▼────┐ ┌▼──────────┐
    │ Ghana  │ │Kenya │ │Nigeria│ │ Medical    │
    │ Plugin │ │Plugin│ │Plugin │ │ Domain     │
    │(ref)   │ │(stub)│ │(stub) │ │ (stub)     │
    └────────┘ └──────┘ └──────┘ └────────────┘
```

## 3. Plugin Manifest (JSON Schema)

Each plugin ships a `domain.json` manifest. The Legal Brain core reads this on startup
and registers the domain.

### Schema

```json
{
  "$schema": "https://kai.localhost/schemas/domain-plugin-v1.json",
  "id": "ghana-legal-brain",
  "domain_type": "legal",
  "jurisdiction": "Ghana",
  "name": "Ghana Legal Brain",
  "version": "1.0.0",
  "enabled": true,
  "description": "Ghana legal research assistant — constitution, legislation, case law",
  "author": "Kai AI Orchestrator",
  "created": "2026-08-07",

  "storage": {
    "db_type": "postgresql",
    "db_name": "klaus_ghana",
    "vector_store": "pgvector",
    "knowledge_graph_db": "kg_ghana",
    "permanent_storage_root": "/var/lib/ai-orchestrator/legal_brain/ghana/permanent",
    "workspace_root": "/var/lib/ai-orchestrator/legal_brain/ghana/workspace"
  },

  "enums": {
    "courts": ["Supreme Court", "Court of Appeal", "High Court", "Circuit Court", "District Court", "Specialised Court"],
    "legislation_types": ["Constitution", "Act of Parliament", "Legislative Instrument (LI)", "Constitutional Instrument (CI)", "Regulation", "Bye-law"],
    "document_statuses": ["current", "overruled", "amended", "repealed", "historical", "superseded", "stayed"],
    "taxonomy_categories": [
      {"code": "01", "name": "Constitution"},
      {"code": "02", "name": "Legislation"},
      {"code": "03", "name": "Case Law"},
      {"code": "04", "name": "Legal Instruments"},
      {"code": "05", "name": "International Law"},
      {"code": "06", "name": "Customary Law"},
      {"code": "07", "name": "Commentary"}
    ]
  },

  "citation": {
    "parser_module": "core.legal_brain.plugins.ghana.citation",
    "format_examples": [
      "[2003-2004] SCGLR 1",
      "Act 651 (Labour Act, 2003)",
      "LI 1807 (2002)"
    ]
  },

  "qc_agents": {
    "module": "core.legal_brain.plugins.ghana.qc",
    "agents": [
      "source_verification",
      "classification_accuracy",
      "duplicate_detection",
      "outdated_law_detection"
    ],
    "known_sources": [
      "parliament.gh",
      "judiciary.gov.gh",
      "ghalii.org",
      "laws.ghanalegal.com"
    ]
  },

  "knowledge_sources": [
    {
      "name": "Parliament of Ghana",
      "url": "https://parliament.gh",
      "tier": 1,
      "type": "legislation",
      "language": "en"
    },
    {
      "name": "Judicial Service of Ghana",
      "url": "https://judiciary.gov.gh",
      "tier": 1,
      "type": "case_law",
      "language": "en"
    }
  ],

  "capabilities": {
    "chat": true,
    "research_sessions": true,
    "document_upload": true,
    "qc_pipeline": true,
    "subscription_billing": true,
    "mobile_money": "hubtel"
  }
}
```

### Domain types

| Domain Type | Jurisdiction Field | Example |
|-------------|-------------------|---------|
| `legal` | country name | Ghana, Kenya, Nigeria |
| `medical` | specialty | General Practice, Cardiology |
| `family` | jurisdiction | Ghana Family Law, Kenya Family Law |
| `finance` | regulation | SEC Ghana, CMA Kenya |

## 4. Plugin Interface (Python ABC)

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


@dataclass
class PluginManifest:
    """Deserialized from domain.json."""
    id: str
    domain_type: str
    jurisdiction: str
    name: str
    version: str
    enabled: bool
    description: str
    storage: Dict[str, Any]
    enums: Dict[str, Any]
    citation: Dict[str, Any]
    qc_agents: Dict[str, Any]
    knowledge_sources: List[Dict[str, Any]]
    capabilities: Dict[str, bool]


class DomainPlugin(ABC):
    """Interface every jurisdiction/domain plugin must implement."""

    @abstractmethod
    def get_manifest(self) -> PluginManifest:
        """Return the plugin manifest."""

    @abstractmethod
    def get_court_hierarchy(self) -> List[str]:
        """Ordered list of court levels (highest first)."""

    @abstractmethod
    def get_legislation_types(self) -> List[str]:
        """Valid legislation types for this jurisdiction."""

    @abstractmethod
    def parse_citation(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse a citation string into structured fields."""

    @abstractmethod
    def get_metadata_class(self):
        """Return the jurisdiction-specific LegalMetadata dataclass/class."""

    @abstractmethod
    def get_qc_agents(self) -> List[Any]:
        """Return QC agent instances for this domain."""

    @abstractmethod
    def get_knowledge_sources(self) -> List[Dict[str, Any]]:
        """Return configured knowledge sources with tier ratings."""

    @abstractmethod
    def get_storage_config(self) -> Dict[str, Any]:
        """Return storage paths and DB names for this domain."""

    @abstractmethod
    def validate_document(self, document: Dict[str, Any]) -> List[str]:
        """Validate a document against jurisdiction-specific rules.
        Returns list of validation errors (empty = valid)."""

    # ── Optional hooks ──

    def on_activate(self) -> None:
        """Called when the operator activates this domain."""

    def on_deactivate(self) -> None:
        """Called when the operator deactivates this domain."""

    def get_custom_commands(self) -> Dict[str, Any]:
        """Optional domain-specific bot commands."""
        return {}

    def get_prompt_override(self) -> Optional[str]:
        """Optional system prompt override for the domain's bot."""
        return None
```

## 5. Plugin Registry

```python
# core/legal_brain/registry.py

from pathlib import Path
from typing import Dict
import json

_registry: Dict[str, DomainPlugin] = {}
_manifests: Dict[str, PluginManifest] = {}


def register_plugin(plugin: DomainPlugin) -> None:
    """Register a domain plugin. Called at startup for each enabled plugin."""
    manifest = plugin.get_manifest()
    if manifest.id in _registry:
        raise ValueError(f"Plugin '{manifest.id}' already registered")
    _registry[manifest.id] = plugin
    _manifests[manifest.id] = manifest


def get_plugin(domain_id: str) -> Optional[DomainPlugin]:
    """Get an active plugin by ID."""
    return _registry.get(domain_id)


def list_plugins(domain_type: Optional[str] = None) -> Dict[str, PluginManifest]:
    """List all registered plugins, optionally filtered by domain_type."""
    if domain_type:
        return {k: v for k, v in _manifests.items() if v.domain_type == domain_type}
    return dict(_manifests)


def get_active_jurisdictions() -> List[str]:
    """List all currently enabled jurisdictions."""
    return [m.jurisdiction for m in _manifests.values() if m.enabled]


def activate_plugin(domain_id: str) -> bool:
    """Enable a plugin and run its on_activate hook."""
    plugin = _registry.get(domain_id)
    if not plugin:
        return False
    plugin.get_manifest().enabled = True
    plugin.on_activate()
    return True


def deactivate_plugin(domain_id: str) -> bool:
    """Disable a plugin and run its on_deactivate hook."""
    plugin = _registry.get(domain_id)
    if not plugin:
        return False
    plugin.on_deactivate()
    plugin.get_manifest().enabled = False
    return True
```

## 6. Storage Isolation

Each domain gets dedicated storage, enforced by the Storage Router:

```
/var/lib/ai-orchestrator/legal_brain/
├── ghana/                        # Ghana Legal Brain (reference plugin)
│   ├── permanent/
│   │   ├── legal_brain.db        # Permanent SQLite (immutable content)
│   │   └── documents/            # Content-addressed file storage
│   ├── workspace/                # Temporary workspace (TTL-gated)
│   ├── knowledge/
│   │   └── knowledge_graph.db    # Domain-specific KG
│   └── domain.json               # Plugin manifest (read at startup)
│
├── kenya/                        # Kenya Legal Brain (stub — disabled)
│   ├── domain.json
│   └── (empty until activated)
│
├── medical/                      # Medical Domain (stub — disabled)
│   └── domain.json
│
├── family/                       # Family Law Domain (stub — disabled)
│   └── domain.json
│
└── finance/                      # Finance Domain (stub — disabled)
    └── domain.json
```

### PostgreSQL isolation (for KLAUS)

When KLAUS's PostgreSQL is available, each domain gets its own database:

- `klaus_ghana` — Ghana tables (sources, documents, chunks, embeddings)
- `klaus_kenya` — Kenya tables (created on activation)
- Query routing: `get_db_for_jurisdiction("Kenya")` → connect to `klaus_kenya`

### SQLite fallback isolation

When PostgreSQL is unavailable, each domain uses separate SQLite files:

- `/var/lib/ai-orchestrator/legal_brain/ghana/permanent/legal_brain.db`
- `/var/lib/ai-orchestrator/legal_brain/kenya/permanent/legal_brain.db`

The `ImmutableStorage` class already accepts `storage_dir` and `db_path` parameters,
so isolation is a matter of passing different paths per domain.

## 7. QC Agent Dispatch

The core QC orchestrator (`klaus/quality_agents.py`) dispatches to jurisdiction-specific
agents based on document jurisdiction:

```python
def run_qc_pipeline(document: Dict[str, Any]) -> QCReport:
    jurisdiction = document.get("jurisdiction", "Ghana")
    plugin = get_plugin_for_jurisdiction(jurisdiction)
    agents = plugin.get_qc_agents()

    findings = []
    for agent in agents:
        result = agent.review(document)
        findings.extend(result.findings)

    return aggregate_findings(findings)
```

Jurisdiction-agnostic agents (source verification, duplicate detection) ship in core.
Jurisdiction-specific agents (citation format, court hierarchy validation, outdated law
detection against jurisdiction-specific amendment chains) ship in plugins.

## 8. Migration Path (Ghana Hardcoded → Plugin)

Phase 1 of migration makes the Ghana plugin the *first* plugin without changing behavior:

1. **Create plugin directory** — `core/legal_brain/plugins/ghana/`
2. **Extract Ghana-specific enums** — move `CourtLevel`, `LegislationType`, etc. into the plugin
3. **Implement `GhanaLegalPlugin(DomainPlugin)`** — wrapping existing `legal_metadata.py` and `legal_qc.py`
4. **Write `domain.json`** for Ghana
5. **Auto-register Ghana at startup** — `if no plugins registered, register Ghana as default`
6. **Replace `ALLOWED_JURISDICTIONS`** with registry lookup: `get_active_jurisdictions()`
7. **Remove all hardcoded `"Ghana"` defaults** from core modules

This is a refactoring-only change. No new functionality. All existing tests must pass
before and after.

## 9. Stub Domains

Each stub is a `domain.json` with `"enabled": false` plus empty directory structure.
They serve as templates for future implementation.

### Kenya Legal Brain (`core/legal_brain/plugins/kenya/domain.json`)

```json
{
  "id": "kenya-legal-brain",
  "domain_type": "legal",
  "jurisdiction": "Kenya",
  "name": "Kenya Legal Brain",
  "version": "0.1.0",
  "enabled": false,
  "description": "Kenya legal research assistant — Constitution of Kenya 2010, Acts of Parliament, case law from eKLR",
  "storage": {
    "db_name": "klaus_kenya",
    "permanent_storage_root": "/var/lib/ai-orchestrator/legal_brain/kenya/permanent",
    "workspace_root": "/var/lib/ai-orchestrator/legal_brain/kenya/workspace"
  },
  "enums": {
    "courts": ["Supreme Court", "Court of Appeal", "High Court", "Employment and Labour Relations Court", "Environment and Land Court", "Magistrates Court", "Kadhis Court"],
    "legislation_types": ["Constitution", "Act of Parliament", "Legal Notice", "Regulation", "Bill", "Treaty"],
    "taxonomy_categories": [
      {"code": "01", "name": "Constitution of Kenya 2010"},
      {"code": "02", "name": "Legislation"},
      {"code": "03", "name": "Case Law"},
      {"code": "04", "name": "Gazette Notices"},
      {"code": "05", "name": "International Treaties"},
      {"code": "06", "name": "Customary Law"},
      {"code": "07", "name": "Commentary"}
    ]
  },
  "citation": {
    "format_examples": [
      "Petition No. 1 of 2017 (Supreme Court)",
      "Civil Appeal No. 105 of 2018",
      "Kenya Gazette Notice No. 1234"
    ]
  },
  "knowledge_sources": [
    {"name": "Kenya Law (eKLR)", "url": "http://kenyalaw.org", "tier": 1, "type": "case_law"},
    {"name": "Parliament of Kenya", "url": "http://parliament.go.ke", "tier": 1, "type": "legislation"},
    {"name": "Kenya Gazette", "url": "http://kenyagazette.go.ke", "tier": 1, "type": "gazette"}
  ],
  "capabilities": {
    "chat": true,
    "research_sessions": true,
    "qc_pipeline": true,
    "mobile_money": "mpesa"
  }
}
```

### Medical Domain (`core/legal_brain/plugins/medical/domain.json`)

A non-legal domain demonstrating the architecture's extensibility:

```json
{
  "id": "medical-ghana",
  "domain_type": "medical",
  "jurisdiction": "Ghana",
  "name": "Ghana Medical Knowledge Base",
  "version": "0.1.0",
  "enabled": false,
  "description": "Medical knowledge base — Ghana Health Service guidelines, drug registry, treatment protocols",
  "storage": {
    "db_name": "medical_ghana",
    "permanent_storage_root": "/var/lib/ai-orchestrator/legal_brain/medical/permanent"
  },
  "enums": {
    "specialties": ["General Practice", "Cardiology", "Pediatrics", "Obstetrics", "Surgery", "Public Health"],
    "source_types": ["clinical_guideline", "drug_monograph", "treatment_protocol", "research_paper", "public_health_advisory"]
  },
  "citation": {
    "format_examples": ["GHS Standard Treatment Guidelines (2023)", "FDA Ghana Drug Register No. XYZ"]
  },
  "knowledge_sources": [
    {"name": "Ghana Health Service", "url": "https://ghs.gov.gh", "tier": 1, "type": "clinical_guideline"},
    {"name": "FDA Ghana", "url": "https://fdaghana.gov.gh", "tier": 1, "type": "drug_registry"}
  ],
  "capabilities": {
    "chat": true,
    "document_upload": true,
    "qc_pipeline": false
  }
}
```

Similarly, stub manifests for `family` (Family Law) and `finance` (Financial Regulation)
domains follow the same pattern — a `domain.json` with `"enabled": false`, domain-specific
enums, source registries, and storage configs. They are NOT implemented; only the manifest
exists to guide future work.

## 10. API Endpoints

New endpoints for domain plugin management (extending `core/api.py`):

```
GET  /api/legal/domains                 — list all registered domains + enabled status
POST /api/legal/domains/{id}/activate   — enable a domain (admin-gated)
POST /api/legal/domains/{id}/deactivate — disable a domain (admin-gated)
GET  /api/legal/domains/{id}/sources    — list knowledge sources for a domain
GET  /api/legal/domains/{id}/manifest   — return the full manifest for a domain
```

The `upload_law_document_endpoint` already accepts `jurisdiction` as an optional form
field. After this phase, the jurisdiction dropdown on the dashboard should reflect
`get_active_jurisdictions()` rather than being hardcoded to Ghana.

## 11. Security Boundaries

1. **Domain DB isolation is enforced by config, not code.** No cross-domain JOINs.
   The storage router refuses queries that cross domain boundaries.
2. **Plugin code runs in the same process as core.** Plugins are trusted Python modules,
   not arbitrary code. The operator reviews plugin code before activation (same bar
   as any other Kai module).
3. **Activation is admin-gated.** `_require_write_capability("law.manage")` guards
   activation/deactivation endpoints. The operator controls which jurisdictions are live.
4. **Stub domains ship disabled.** A fresh deploy has only Ghana enabled. The operator
   must explicitly activate Kenya or any other domain.
5. **Sandboxing (future).** When K3 workspace sandboxing ships, each domain's QC pipeline
   can run in its own sandbox for defense-in-depth. Not required for v1.

## 12. Testing Strategy

1. **Plugin interface contract tests** — every plugin must pass a shared test suite
   verifying that `get_manifest()`, `get_court_hierarchy()`, etc. return valid data.
2. **Storage isolation tests** — verify that writing to Ghana's DB does not leak into
   Kenya's DB and vice versa.
3. **Registry tests** — activate/deactivate plugins, verify `get_active_jurisdictions()`
   reflects changes.
4. **Migration tests** — verify that Ghana plugin wrapping does not change behavior
   of existing `legal_metadata.py` and `legal_qc.py` functions.
5. **Stub validation** — every `domain.json` passes schema validation.

## 13. Implementation Phases (NOT part of 18F — deferred)

18F is design-only. Implementation is split into future phases:

| Phase | Description | Effort |
|-------|-------------|--------|
| 18F-IMPL-1 | Plugin registry + ABC + manifest loader | Small |
| 18F-IMPL-2 | Extract Ghana into reference plugin | Medium |
| 18F-IMPL-3 | Storage router with per-domain DB isolation | Medium |
| 18F-IMPL-4 | QC agent dispatch by jurisdiction | Small |
| 18F-IMPL-5 | API endpoints for domain management | Small |
| 18F-IMPL-6 | Dashboard domain selector | Small |
| 18F-IMPL-7 | Kentya plugin implementation | Large (deferred) |

## 14. Open Questions

1. **Should plugins be hot-reloadable?** Currently, plugins load at startup. Hot-reload
   adds complexity. Recommendation: require restart on plugin changes (operator action
   is rare enough that this is acceptable).
2. **Should non-legal domains live under `legal_brain/`?** The directory is named
   `legal_brain` but the architecture supports medical/family/finance. Recommendation:
   rename the root to `kai_domains/` or keep as-is with documentation that it's a
   "domain plugin system" not strictly legal.
3. **Vector store per domain?** pgvector supports multiple tables within one DB.
   Recommendation: one pgvector extension per PostgreSQL cluster, separate tables
   per domain (not separate clusters). Embedding model is the same across domains.
