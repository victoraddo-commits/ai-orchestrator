# Universal Website Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize KAI's account registration from hand-written per-site adapters to a universal browser-driven system that classifies any domain, learns and stores a reviewable per-domain Site Recipe, and drives the existing onboarding state machine plus browser/mail/SMS/human-takeover machinery to register an account offline across three distinct signup shapes.

**Architecture:** The onboarding engine is unchanged in shape. At `SELECT_PROVIDER_ADAPTER` it resolves a registered exact/seeded adapter first and otherwise builds a `GenericWebAdapter(domain)`. The adapter synthesizes its descriptor from a `CapabilityClassifier` `SiteProfile`; it uses a published `SiteRecipe` when present, otherwise drives a `RecipeLearner` (reasoning over the browser DOM) to store a draft recipe and pause for review. Drift (a recipe selector that no longer resolves) flips the recipe to `stale`, re-learns, and pauses. Recipes are versioned JSON under `memory/site_recipes/<domain>.json`, contain no secrets, and are exposed read-only (plus a publish action) through the Command Center. Automation policy defaults to `UNKNOWN` -> human confirmation, and no CAPTCHA/anti-bot bypass or live-site run is implemented.

**Tech Stack:** Python 3, Pydantic v2 (`extra="forbid"` schemas), pytest, FastAPI `TestClient`, FastAPI `APIRouter`, existing `core.providers` / `core.onboarding` / `core.browser` / `core.notify` / `core.memory` primitives, vanilla HTML/JS Command Center panel.

---

## Environment

All work happens on **CT111** (`/opt/ai-orchestrator`, branch `runner-kai-2.0-20260918`, `.venv`). Reach it with:

```bash
ssh -i /root/.ssh/pve2_deploy -o BatchMode=yes root@192.168.1.110
pct exec 111 -- bash -lc 'cd /opt/ai-orchestrator && <command>'
```

Run tests with the repo venv: `.venv/bin/python -m pytest ...`.

## Constraints (encode in every task)

- Automation policy defaults to `UNKNOWN` -> **human confirmation** before any automated step.
- **No secrets** in recipes or the recipe store (selectors/URLs/metadata only; value *sources* are symbolic strings such as `identity.email` or `generated_password`).
- **No CAPTCHA / anti-bot bypass**; those route to human takeover.
- **No live-site runs** in any test; everything is offline fixtures/stubs.
- **TDD, 80%+ coverage**, frequent commits with conventional messages.

## File-Structure Map

| Path | Responsibility |
|---|---|
| `core/site_recipes/__init__.py` | Package marker; re-exports schema symbols. |
| `core/site_recipes/schema.py` | `SiteRecipe`, `SiteProfile`, `SiteRequirements`, `FieldSpec`, `RecipeStep`, enums `FlowType`/`RecipeStatus`/`RecipeSource`, `normalize_domain`, `now_iso`. No secrets. |
| `core/site_recipes/store.py` | Versioned CRUD over `memory/site_recipes/<domain>.json`; draft->published promotion; `list_recipes`/`list_stale`; secret guard at the storage boundary. Mirrors `core/providers/store.py`. |
| `core/site_recipes/learner.py` | `RecipeLearner`: reasoning over a browser DOM/a11y snapshot -> draft `SiteRecipe` (`source=learned`, `status=draft`). |
| `core/discovery/__init__.py` | Package marker. |
| `core/discovery/reasoning.py` | `ReasoningBackend` protocol + `FixtureReasoningBackend` (deterministic tests) + `LocalModelReasoningBackend` (Kai local fabric; documented, not required in tests). |
| `core/discovery/classifier.py` | `CapabilityClassifier`: URL/domain -> `SiteProfile` via heuristics + pluggable reasoning hook; policy default `UNKNOWN`. |
| `core/providers/generic_web.py` | `GenericWebAdapter(ProviderAdapter)` + `build_generic_adapter(domain, ...)`: descriptor from `SiteProfile`, published-recipe-else-learn, operations -> browser operator, drift -> stale + re-learn, no-secret guard. |
| `core/onboarding/engine.py` | Modify `_h_select_provider_adapter` (exact adapter else `GenericWebAdapter`) and `_h_start_registration` (honor adapter `requires_human` -> pause). |
| `tests/site_fixtures.py` | Offline test support: `load_site`, `FakeSiteBrowser`, `FakeVault`, `descriptor_from_site`, `recipe_from_site`, `make_adapter`. |
| `tests/fixtures/sites/single_page.json` | Offline single-page signup fixture (pages + seeded recipe). |
| `tests/fixtures/sites/multi_step.json` | Offline multi-step wizard fixture. |
| `tests/fixtures/sites/sso_invite.json` | Offline SSO / email-invite fixture. |
| `core/cc_account_routes.py` | Modify: add `GET /api/site-recipes`, `GET /api/site-recipes/{domain}`, `POST /api/site-recipes/{domain}/publish` with redacting shaping. |
| `core/kai/command_center.html` | Modify: add a `site-recipes` panel (nav item, section, `loadSiteRecipes` loader, `PANEL_LOADERS` entry). |
| `tests/test_site_recipes_schema.py` | Schema unit tests. |
| `tests/test_site_recipes_store.py` | Store versioning/publish/stale/no-secrets tests. |
| `tests/test_discovery_reasoning.py` | Reasoning backend tests. |
| `tests/test_discovery_classifier.py` | Classifier heuristic + policy-UNKNOWN tests. |
| `tests/test_site_recipes_learner.py` | Learner draft-recipe tests. |
| `tests/test_generic_web_adapter.py` | Adapter descriptor, published-recipe driving, draft-learn pause, drift tests. |
| `tests/test_generic_web_engine.py` | Engine `SELECT_PROVIDER_ADAPTER` fallback + pause-on-`requires_human` tests. |
| `tests/test_universal_website_e2e.py` | Per-shape offline E2E -> COMPLETED. |
| `tests/test_site_recipe_drift.py` | Drift -> stale + re-learn + engine pause. |
| `tests/test_site_recipes_api.py` | CC recipe-viewer API + leak-prevention tests. |

---

## Task 1: Site Recipe schema

**Files:**
- Create: `core/site_recipes/__init__.py`
- Create: `core/site_recipes/schema.py`
- Test: `tests/test_site_recipes_schema.py`

- [ ] **Step 1: Write the failing test**

```python
"""core.site_recipes.schema - schema unit tests."""

import pytest
from pydantic import ValidationError

from core.providers.schema import AutomationPolicy
from core.site_recipes.schema import (
    FieldSpec,
    FlowType,
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
    SiteRequirements,
    normalize_domain,
)


def test_normalize_domain_strips_scheme_path_and_www():
    assert normalize_domain("https://www.Example.com/signup?x=1") == "example.com"
    assert normalize_domain("Example.com:443") == "example.com"
    assert normalize_domain("user@example.com") == "example.com"


def test_site_recipe_defaults_are_safe():
    recipe = SiteRecipe(domain="example.com")
    assert recipe.flow_type is FlowType.unknown
    assert recipe.status is RecipeStatus.draft
    assert recipe.source is RecipeSource.learned
    assert recipe.version == 1
    assert recipe.requirements == SiteRequirements()
    assert recipe.fields == [] and recipe.steps == []


def test_site_recipe_forbids_extra_fields():
    with pytest.raises(ValidationError):
        SiteRecipe(domain="example.com", password="hunter2")


def test_site_recipe_rejects_bad_domain():
    with pytest.raises(ValidationError):
        SiteRecipe(domain="not a domain")


def test_field_and_step_specs_validate():
    field = FieldSpec(name="Email", selector="#email", kind="email",
                      value_source="identity.email")
    step = RecipeStep(index=1, action="fill", selector="#email",
                      value_source="identity.email")
    recipe = SiteRecipe(domain="example.com", fields=[field], steps=[step])
    assert recipe.fields[0].value_source == "identity.email"
    assert recipe.steps[0].action == "fill"


def test_site_profile_defaults_policy_unknown():
    profile = SiteProfile(domain="unknown.example")
    assert profile.automation_policy is AutomationPolicy.UNKNOWN
    assert profile.registrable is True
    assert profile.flow_type is FlowType.unknown
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_schema.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.site_recipes'`

- [ ] **Step 3: Write minimal implementation**

`core/site_recipes/__init__.py`:

```python
"""core.site_recipes - universal website registration recipes.

A *Site Recipe* is the reviewable, secret-free, per-domain plan for how to
register an account on a website: flow type, fields, requirements, ordered
browser steps, verification flow and confidence. It is produced either by a
human (``source=seeded``) or by ``RecipeLearner`` (``source=learned``), and is
promoted ``draft -> published`` by an operator before it drives a live run.
"""

from core.site_recipes.schema import (  # noqa: F401
    FieldSpec,
    FlowType,
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
    SiteRequirements,
    normalize_domain,
    now_iso,
)

__all__ = [
    "FieldSpec",
    "FlowType",
    "RecipeSource",
    "RecipeStatus",
    "RecipeStep",
    "SiteProfile",
    "SiteRecipe",
    "SiteRequirements",
    "normalize_domain",
    "now_iso",
]
```

`core/site_recipes/schema.py`:

```python
"""Site Recipe / Site Profile schemas (Pydantic v2).

A recipe is *metadata only*: domains, URLs, selectors, symbolic value sources
and booleans. It MUST NOT contain a credential value. ``extra="forbid"``
enforces that at the model edge, mirroring ``core.providers.schema``.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.providers.schema import AutomationPolicy

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]+[.][A-Za-z0-9-]+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_domain(value: str) -> str:
    """Lower-case host from a URL/domain/email; strip scheme, path, port, www."""
    raw = (value or "").strip().lower()
    raw = _SCHEME_RE.sub("", raw)
    raw = raw.split("@")[-1]
    raw = raw.split("/")[0]
    raw = raw.split(":")[0]
    if raw.startswith("www."):
        raw = raw[4:]
    return raw


class FlowType(str, enum.Enum):
    single_page = "single_page"
    multi_step = "multi_step"
    sso_only = "sso_only"
    invite_only = "invite_only"
    unknown = "unknown"


class RecipeStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    stale = "stale"


class RecipeSource(str, enum.Enum):
    seeded = "seeded"
    learned = "learned"


class SiteRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: bool = False
    phone: bool = False
    captcha: bool = False
    mfa: bool = False
    payment: bool = False
    kyc: bool = False
    email_verify: bool = False
    phone_verify: bool = False


class FieldSpec(BaseModel):
    """One form field. ``value_source`` is symbolic (never a secret value)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    selector: str = Field(min_length=1, max_length=300)
    kind: str = "text"
    required: bool = True
    value_source: Optional[str] = None
    step: int = 0


class RecipeStep(BaseModel):
    """One browser-operator step. ``value_source`` is symbolic, never a value."""

    model_config = ConfigDict(extra="forbid")

    index: int = 0
    action: str
    selector: Optional[str] = None
    url: Optional[str] = None
    value_source: Optional[str] = None
    description: Optional[str] = None


class SiteRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    signup_url: Optional[str] = None
    flow_type: FlowType = FlowType.unknown
    fields: list[FieldSpec] = Field(default_factory=list)
    requirements: SiteRequirements = Field(default_factory=SiteRequirements)
    steps: list[RecipeStep] = Field(default_factory=list)
    verification_flow: Optional[str] = None
    confidence: float = 0.0
    source: RecipeSource = RecipeSource.learned
    status: RecipeStatus = RecipeStatus.draft
    version: int = 1
    last_verified_at: Optional[str] = None
    evidence_ref: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: str) -> str:
        normalized = normalize_domain(value)
        if not _DOMAIN_RE.match(normalized):
            raise ValueError(f"invalid domain: {value!r}")
        return normalized


class SiteProfile(BaseModel):
    """What the classifier knows about a domain before any recipe exists."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    signup_url: Optional[str] = None
    registrable: bool = True
    flow_type: FlowType = FlowType.unknown
    requirements: SiteRequirements = Field(default_factory=SiteRequirements)
    automation_policy: AutomationPolicy = AutomationPolicy.UNKNOWN
    policy_source: Optional[str] = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _check_domain(cls, value: str) -> str:
        normalized = normalize_domain(value)
        if not _DOMAIN_RE.match(normalized):
            raise ValueError(f"invalid domain: {value!r}")
        return normalized
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_schema.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add core/site_recipes/__init__.py core/site_recipes/schema.py tests/test_site_recipes_schema.py
git commit -m "feat(site_recipes): add secret-free SiteRecipe/SiteProfile schemas"
```

---

## Task 2: Versioned recipe store

**Files:**
- Create: `core/site_recipes/store.py`
- Test: `tests/test_site_recipes_store.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.site_recipes.store'`

- [ ] **Step 3: Write minimal implementation**

`core/site_recipes/store.py`:

```python
"""Versioned Site Recipe store.

One JSON file per domain under ``memory/site_recipes/<domain>.json`` holding a
``{"domain", "versions": [...]}`` envelope. Writes go through the canonical
atomic primitive ``core.memory.update`` (fcntl.flock + ``.bak`` + ``os.replace``)
with :func:`core.secret_guard.assert_no_secret_fields` at the storage boundary,
mirroring ``core.providers.store``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from core.memory import load, update
from core.secret_guard import assert_no_secret_fields

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


def read_versions(domain: str) -> list[dict]:
    return _records(load(recipe_name(domain), directory=memory_dir()))


def _mutate_versions(domain: str, mutate_fn: Callable[[list[dict]], list[dict]]):
    (memory_dir() / "site_recipes").mkdir(parents=True, exist_ok=True)

    def _mutate(data):
        versions = mutate_fn(_records(data))
        assert_no_secret_fields(versions)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_store.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add core/site_recipes/store.py tests/test_site_recipes_store.py
git commit -m "feat(site_recipes): add versioned recipe store with publish/stale"
```

---

## Task 3: Reasoning backends

**Files:**
- Create: `core/discovery/__init__.py`
- Create: `core/discovery/reasoning.py`
- Test: `tests/test_discovery_reasoning.py`

- [ ] **Step 1: Write the failing test**

```python
"""core.discovery.reasoning - protocol + fixture + local backends."""

from core.discovery.reasoning import (
    FixtureReasoningBackend,
    LocalModelReasoningBackend,
    ReasoningBackend,
)
from core.site_recipes.schema import SiteProfile


def test_fixture_backend_returns_registered_profile():
    backend = FixtureReasoningBackend(
        profiles={"x.example": {"flow_type": "sso_only", "confidence": 0.8}})
    assert backend.propose_profile("x.example", "https://x.example") == {
        "flow_type": "sso_only", "confidence": 0.8}
    assert backend.propose_profile("y.example", "https://y.example") is None


def test_fixture_backend_returns_registered_recipe():
    backend = FixtureReasoningBackend(
        recipes={"x.example": {"steps": [{"index": 0, "action": "navigate",
                                          "url": "https://x.example/signup"}]}})
    proposal = backend.propose_recipe(SiteProfile(domain="x.example"),
                                      {"title": "Sign up"})
    assert proposal["steps"][0]["action"] == "navigate"


def test_local_model_backend_returns_none_until_enabled():
    backend = LocalModelReasoningBackend()
    assert backend.propose_profile("x.example", "https://x.example") is None
    assert backend.propose_recipe(SiteProfile(domain="x.example"), {}) is None


def test_fixture_backend_satisfies_protocol():
    assert isinstance(FixtureReasoningBackend(), ReasoningBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_reasoning.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.discovery'`

- [ ] **Step 3: Write minimal implementation**

`core/discovery/__init__.py`:

```python
"""core.discovery - domain classification and reasoning for universal web registration."""

from core.discovery.reasoning import (  # noqa: F401
    FixtureReasoningBackend,
    LocalModelReasoningBackend,
    ReasoningBackend,
)

__all__ = [
    "ReasoningBackend",
    "FixtureReasoningBackend",
    "LocalModelReasoningBackend",
]
```

`core/discovery/reasoning.py`:

```python
"""Pluggable reasoning backends for site classification and recipe learning.

The production backend is :class:`LocalModelReasoningBackend`, wired to the Kai
local model fabric (Ollama ``qwen3-coder:kai`` on VM104). It is documented here
but returns ``None`` until the fabric hook is enabled, so the default pipeline
never depends on a model for correctness. :class:`FixtureReasoningBackend`
provides deterministic, fully offline proposals for the test suite.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from core.site_recipes.schema import SiteProfile


@runtime_checkable
class ReasoningBackend(Protocol):
    def propose_profile(self, domain: str, target: str) -> Optional[dict]:
        ...

    def propose_recipe(self, profile: SiteProfile, snapshot: dict) -> Optional[dict]:
        ...


class FixtureReasoningBackend:
    """Deterministic in-memory backend keyed by domain (tests only)."""

    def __init__(self, profiles: Optional[dict] = None,
                 recipes: Optional[dict] = None):
        self.profiles = dict(profiles or {})
        self.recipes = dict(recipes or {})

    def propose_profile(self, domain: str, target: str) -> Optional[dict]:
        return self.profiles.get(domain)

    def propose_recipe(self, profile: SiteProfile, snapshot: dict) -> Optional[dict]:
        return self.recipes.get(profile.domain)


class LocalModelReasoningBackend:
    """Kai local model fabric backend (documented; not required in tests).

    A future hook posts the sanitized ``SiteProfile`` / page snapshot to the
    local model endpoint and parses a strict JSON proposal. Until that hook is
    enabled it is a no-op, so callers fall back to heuristics.
    """

    def __init__(self, model: str = "qwen3-coder:kai",
                 endpoint: str = "http://127.0.0.1:11434"):
        self.model = model
        self.endpoint = endpoint

    def propose_profile(self, domain: str, target: str) -> Optional[dict]:
        # No external call: the fabric hook is deliberately not wired yet.
        return None

    def propose_recipe(self, profile: SiteProfile, snapshot: dict) -> Optional[dict]:
        return None


__all__ = ["ReasoningBackend", "FixtureReasoningBackend",
           "LocalModelReasoningBackend"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_reasoning.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add core/discovery/__init__.py core/discovery/reasoning.py tests/test_discovery_reasoning.py
git commit -m "feat(discovery): add pluggable reasoning backends"
```

---

## Task 4: Capability classifier

**Files:**
- Create: `core/discovery/classifier.py`
- Test: `tests/test_discovery_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
"""core.discovery.classifier - URL/domain -> SiteProfile heuristics."""

import pytest

from core.discovery.classifier import CapabilityClassifier
from core.discovery.reasoning import FixtureReasoningBackend
from core.providers import (
    AutomationPolicy,
    HumanConfirmationRequired,
    ensure_automation_allowed,
    register_provider,
)
from core.providers.schema import ProviderDescriptor


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    return tmp_path


def test_classifies_default_signup_shape():
    profile = CapabilityClassifier().classify("https://example.com/signup")
    assert profile.domain == "example.com"
    assert profile.flow_type.value == "single_page"
    assert profile.requirements.email is True
    assert profile.requirements.email_verify is True
    assert profile.automation_policy is AutomationPolicy.UNKNOWN
    assert profile.policy_source


def test_recognises_invite_and_sso_paths():
    invite = CapabilityClassifier().classify("https://example.com/invite/abc")
    assert invite.flow_type.value == "invite_only"
    sso = CapabilityClassifier().classify("https://example.com/sso/login")
    assert sso.flow_type.value == "sso_only"


def test_reasoning_backend_overrides_heuristics():
    backend = FixtureReasoningBackend(profiles={
        "special.example": {"flow_type": "multi_step", "confidence": 0.95,
                            "requirements": {"email": True, "phone": True}}})
    profile = CapabilityClassifier(backend=backend).classify("special.example")
    assert profile.flow_type.value == "multi_step"
    assert profile.confidence == 0.95
    assert profile.requirements.phone is True


def test_classified_unknown_policy_requires_human_confirmation(isolated):
    profile = CapabilityClassifier().classify("brand-new.example")
    register_provider(ProviderDescriptor(
        provider_id=profile.domain, display_name=profile.domain,
        official_domain=profile.domain, account_types=["web"],
        automation_policy=profile.automation_policy,
        policy_source=profile.policy_source))
    with pytest.raises(HumanConfirmationRequired) as exc:
        ensure_automation_allowed(profile.domain)
    assert exc.value.decision.automation_policy is AutomationPolicy.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_classifier.py -q`
Expected: FAIL with `ImportError: cannot import name 'CapabilityClassifier'`

- [ ] **Step 3: Write minimal implementation**

`core/discovery/classifier.py`:

```python
"""CapabilityClassifier - URL/domain -> SiteProfile.

Cheap, deterministic heuristics produce a baseline profile; an optional
:class:`ReasoningBackend` may refine it. The automation policy is always left
``UNKNOWN`` by the classifier -- permission is decided by the human-confirmed
policy gate in ``core.providers``, never inferred here.
"""

from __future__ import annotations

from typing import Optional

from core.discovery.reasoning import ReasoningBackend
from core.providers.schema import AutomationPolicy
from core.site_recipes.schema import (
    FlowType,
    SiteProfile,
    SiteRequirements,
    normalize_domain,
)

_SSO_TOKENS = ("sso", "oauth", "signin-with", "sign-in-with", "continue-with")
_INVITE_TOKENS = ("invite", "request-access", "waitlist", "beta")

_DEFAULT_POLICY_SOURCE = (
    "classifier: automation policy not yet evaluated -- human confirmation required"
)


class CapabilityClassifier:
    def __init__(self, backend: Optional[ReasoningBackend] = None,
                 known: Optional[dict] = None):
        self._backend = backend
        self._known = dict(known or {})

    def _heuristic(self, domain: str, target: str) -> SiteProfile:
        path = (target or "").lower()
        profile = SiteProfile(domain=domain, signup_url=f"https://{domain}/signup")
        if any(token in path for token in _INVITE_TOKENS):
            return profile.model_copy(update={
                "flow_type": FlowType.invite_only,
                "requirements": SiteRequirements(email=True, email_verify=True),
            })
        if any(token in path for token in _SSO_TOKENS):
            return profile.model_copy(update={
                "flow_type": FlowType.sso_only,
                "requirements": SiteRequirements(email=True),
            })
        return profile.model_copy(update={
            "flow_type": FlowType.single_page,
            "requirements": SiteRequirements(email=True, email_verify=True),
        })

    def classify(self, target: str) -> SiteProfile:
        domain = normalize_domain(target)
        profile = self._heuristic(domain, target)

        proposal: Optional[dict] = None
        if domain in self._known:
            proposal = self._known[domain].model_dump()
        elif self._backend is not None:
            proposal = self._backend.propose_profile(domain, target)

        if proposal:
            merged = profile.model_dump()
            merged.update({k: v for k, v in proposal.items() if v is not None})
            merged["domain"] = domain
            if merged.get("requirements"):
                merged["requirements"] = SiteRequirements(**merged["requirements"])
            merged["automation_policy"] = AutomationPolicy.UNKNOWN
            merged["policy_source"] = _DEFAULT_POLICY_SOURCE
            profile = SiteProfile(**merged)

        if not profile.policy_source:
            profile = profile.model_copy(update={"policy_source": _DEFAULT_POLICY_SOURCE})
        return profile


__all__ = ["CapabilityClassifier"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_classifier.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add core/discovery/classifier.py tests/test_discovery_classifier.py
git commit -m "feat(discovery): add CapabilityClassifier with UNKNOWN policy default"
```

---

## Task 5: Recipe learner

**Files:**
- Create: `core/site_recipes/learner.py`
- Test: `tests/test_site_recipes_learner.py`

- [ ] **Step 1: Write the failing test**

```python
"""core.site_recipes.learner - DOM reasoning -> draft recipe."""

from core.discovery.reasoning import FixtureReasoningBackend
from core.site_recipes.learner import RecipeLearner
from core.site_recipes.schema import (
    FlowType,
    RecipeSource,
    RecipeStatus,
    SiteProfile,
)


class FakeInspectBrowser:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def perform(self, session_id, op, params=None):
        self.calls.append(op)
        return {"op": op, "snapshot": self.snapshot}


def _profile():
    return SiteProfile(domain="learn.example",
                       signup_url="https://learn.example/signup")


def test_learn_produces_draft_from_backend_proposal():
    backend = FixtureReasoningBackend(recipes={"learn.example": {
        "flow_type": "single_page",
        "steps": [
            {"index": 0, "action": "navigate", "url": "https://learn.example/signup"},
            {"index": 1, "action": "fill", "selector": "#email",
             "value_source": "identity.email"},
            {"index": 2, "action": "click", "selector": "#submit"},
        ],
        "fields": [{"name": "Email", "selector": "#email", "kind": "email",
                    "value_source": "identity.email"}],
        "verification_flow": "email_link",
        "confidence": 0.7,
    }})
    browser = FakeInspectBrowser({"url": "https://learn.example/signup",
                                  "title": "Sign up", "elements": [],
                                  "has_password_field": True, "untrusted": True})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile(),
                                                           session_id="s1")

    assert recipe.domain == "learn.example"
    assert recipe.source is RecipeSource.learned
    assert recipe.status is RecipeStatus.draft
    assert recipe.flow_type is FlowType.single_page
    assert recipe.version == 1
    assert recipe.confidence == 0.7
    assert [s.action for s in recipe.steps] == ["navigate", "fill", "click"]
    assert recipe.fields[0].value_source == "identity.email"
    assert browser.calls == ["inspect"]


def test_learn_without_backend_proposal_yields_empty_draft():
    backend = FixtureReasoningBackend()
    browser = FakeInspectBrowser({"title": "", "elements": []})
    recipe = RecipeLearner(backend, browser=browser).learn(_profile(),
                                                            session_id="s1")
    assert recipe.steps == []
    assert recipe.source is RecipeSource.learned
    assert recipe.status is RecipeStatus.draft
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_learner.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.site_recipes.learner'`

- [ ] **Step 3: Write minimal implementation**

`core/site_recipes/learner.py`:

```python
"""RecipeLearner - propose a draft Site Recipe from a browser DOM/a11y tree.

The learner never persists anything itself and never sees a credential value:
it asks the reasoning backend for a proposal over a *sanitized* snapshot and
maps it into a :class:`SiteRecipe` with ``source=learned``/``status=draft``.
The caller (``GenericWebAdapter``) stores it and pauses for review.
"""

from __future__ import annotations

from typing import Optional

from core.discovery.reasoning import ReasoningBackend
from core.site_recipes.schema import (
    FieldSpec,
    FlowType,
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
    SiteRequirements,
)


class RecipeLearner:
    def __init__(self, backend: ReasoningBackend, browser=None):
        self._backend = backend
        self._browser = browser

    def _inspect(self, session_id, browser=None) -> dict:
        cli = browser or self._browser
        if cli is None or not session_id:
            return {}
        result = cli.perform(session_id, "inspect", {})
        if isinstance(result, dict) and "snapshot" in result:
            return result["snapshot"] or {}
        return result or {}

    def learn(self, profile: SiteProfile, *, session_id: Optional[str] = None,
              browser=None) -> SiteRecipe:
        snapshot = self._inspect(session_id, browser)
        proposal = self._backend.propose_recipe(profile, snapshot) or {}

        steps = [RecipeStep(**step) for step in proposal.get("steps", [])]
        fields = [FieldSpec(**field) for field in proposal.get("fields", [])]
        requirements = proposal.get("requirements")
        flow_value = proposal.get("flow_type", profile.flow_type.value)

        return SiteRecipe(
            domain=profile.domain,
            signup_url=proposal.get("signup_url") or profile.signup_url,
            flow_type=FlowType(flow_value),
            fields=fields,
            steps=steps,
            requirements=(SiteRequirements(**requirements) if requirements
                          else profile.requirements),
            verification_flow=proposal.get("verification_flow"),
            confidence=float(proposal.get("confidence", 0.0)),
            source=RecipeSource.learned,
            status=RecipeStatus.draft,
        )


__all__ = ["RecipeLearner"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_learner.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add core/site_recipes/learner.py tests/test_site_recipes_learner.py
git commit -m "feat(site_recipes): add RecipeLearner producing draft recipes"
```

---

## Task 6: GenericWebAdapter

**Files:**
- Create: `core/providers/generic_web.py`
- Test: `tests/test_generic_web_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
"""core.providers.generic_web - universal adapter behavior (offline)."""

import json

import pytest

from core.discovery.reasoning import FixtureReasoningBackend
from core.providers import AutomationPolicy
from core.providers.adapter import ProviderAdapter
from core.providers.generic_web import GenericWebAdapter, build_generic_adapter
from core.site_recipes import store as recipe_store
from core.site_recipes.learner import RecipeLearner
from core.site_recipes.schema import (
    RecipeSource,
    RecipeStatus,
    RecipeStep,
    SiteProfile,
    SiteRecipe,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


class FakeBrowser:
    def __init__(self, *, fail_selectors=()):
        self.fail = set(fail_selectors)
        self.calls = []

    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        target = params.get("selector") or params.get("url")
        self.calls.append((op, target, params.get("credential", False)))
        if target in self.fail:
            return {"op": op, "ok": False, "error": "selector_not_found"}
        if op == "inspect":
            return {"op": op, "snapshot": {"title": "Sign up", "elements": [],
                                           "has_password_field": True,
                                           "untrusted": True}}
        return {"op": op, "ok": True}


class FakeVault:
    def __init__(self):
        self.writes = {}

    def __call__(self, path, value):
        self.writes[path] = value
        return path


def _published_recipe(domain="site.example"):
    recipe = SiteRecipe(
        domain=domain, signup_url=f"https://{domain}/signup",
        status=RecipeStatus.published, source=RecipeSource.seeded,
        steps=[
            RecipeStep(index=0, action="navigate", url=f"https://{domain}/signup"),
            RecipeStep(index=1, action="fill", selector="#email",
                       value_source="identity.email"),
            RecipeStep(index=2, action="fill", selector="#password",
                       value_source="generated_password"),
            RecipeStep(index=3, action="click", selector="#submit"),
        ])
    recipe_store.save_recipe(recipe)
    return recipe_store.publish_recipe(domain)


def test_adapter_synthesizes_descriptor_from_profile():
    adapter = build_generic_adapter("site.example")
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.descriptor.provider_id == "site.example"
    assert adapter.descriptor.official_domain == "site.example"
    assert adapter.descriptor.browser_required is True
    assert adapter.descriptor.automation_policy is AutomationPolicy.UNKNOWN
    for operation in ("discovery", "registration", "verification",
                      "security_setup", "profile_setup", "account_status"):
        assert callable(getattr(adapter, operation))


def test_registration_drives_published_recipe_and_stores_vault_ref(isolated):
    published = _published_recipe()
    browser = FakeBrowser()
    vault = FakeVault()
    adapter = GenericWebAdapter("site.example", browser=browser, vault=vault,
                                profile=SiteProfile(domain="site.example"))

    outcome = adapter.registration(browser_session_id="sess-1",
                                   identity_id="ident-1", account_id="acct-1")
    assert outcome["status"] == "submitted"
    assert outcome["requires_human"] is False
    assert outcome["recipe_version"] == published.version
    assert outcome["password_ref"] == "secrets/accounts/site.example/acct-1"
    assert set(vault.writes) == {"secrets/accounts/site.example/acct-1"}
    password = vault.writes[outcome["password_ref"]]
    assert len(password) >= 16
    assert password not in json.dumps(outcome)
    # the credential fill is flagged, and the plaintext is never in the calls list
    flagged = [c for c in browser.calls if c[0] == "fill" and c[2] is True]
    assert flagged and flagged[0][1] == "#password"


def test_registration_without_published_recipe_learns_draft_and_pauses(isolated):
    backend = FixtureReasoningBackend(recipes={"new.example": {
        "flow_type": "single_page",
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://new.example/signup"}],
        "confidence": 0.4,
    }})
    browser = FakeBrowser()
    learner = RecipeLearner(backend, browser=browser)
    adapter = GenericWebAdapter("new.example", browser=browser, learner=learner,
                                profile=SiteProfile(domain="new.example"))

    outcome = adapter.registration(browser_session_id="sess-2")
    assert outcome["status"] == "draft_learned"
    assert outcome["requires_human"] is True

    draft = recipe_store.get_recipe("new.example")
    assert draft.status is RecipeStatus.draft
    assert draft.source is RecipeSource.learned
    assert draft.steps[0].action == "navigate"


def test_registration_marks_stale_and_relearns_on_drift(isolated):
    _published_recipe("drift.example")
    backend = FixtureReasoningBackend(recipes={"drift.example": {
        "flow_type": "multi_step", "confidence": 0.5,
        "steps": [{"index": 0, "action": "navigate",
                   "url": "https://drift.example/signup"}],
    }})
    browser = FakeBrowser(fail_selectors={"#email"})
    learner = RecipeLearner(backend, browser=browser)
    adapter = GenericWebAdapter("drift.example", browser=browser, learner=learner,
                                profile=SiteProfile(domain="drift.example"))

    outcome = adapter.registration(browser_session_id="sess-3")
    assert outcome["status"] == "drift_relearned"
    assert outcome["requires_human"] is True
    assert recipe_store.get_recipe("drift.example").status is RecipeStatus.stale
    drafts = [r for r in recipe_store.read_versions("drift.example")
              if r["status"] == "draft"]
    assert drafts and drafts[-1]["source"] == "learned"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generic_web_adapter.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.providers.generic_web'`

- [ ] **Step 3: Write minimal implementation**

`core/providers/generic_web.py`:

```python
"""GenericWebAdapter - the default provider adapter for ANY domain.

The descriptor is synthesized from a :class:`SiteProfile` (produced by
``CapabilityClassifier``). Registration resolves a **published** recipe for the
domain and maps its ordered steps onto the browser operator; when no published
recipe exists it learns a draft and signals a human pause; when a published
step no longer resolves (drift) it marks the recipe ``stale``, re-learns and
pauses. Recipes are secret-free and are re-checked with the storage-boundary
guard before persistence.
"""

from __future__ import annotations

import secrets as _secrets
import string
from typing import Optional

from core.accounts.schema import vault_reference_for
from core.discovery.classifier import CapabilityClassifier
from core.providers.adapter import NOT_APPLICABLE, ProviderAdapter
from core.providers.schema import ProviderDescriptor
from core.secret_guard import assert_no_secret_fields
from core.site_recipes import store as recipe_store
from core.site_recipes.schema import RecipeStep, SiteProfile, normalize_domain

PROVIDER_ACCOUNT_TYPE = "web"

_PASSWORD_SYMBOLS = "!@#$%^&*-_=+"


def _generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + _PASSWORD_SYMBOLS
    while True:
        password = "".join(_secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password) and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)
                and any(c in _PASSWORD_SYMBOLS for c in password)):
            return password


class GenericWebAdapter(ProviderAdapter):
    def __init__(self, domain: str, *, descriptor: Optional[ProviderDescriptor] = None,
                 profile: Optional[SiteProfile] = None, browser=None, vault=None,
                 learner=None, recipe_store_module=None):
        self._domain = normalize_domain(domain)
        self._profile = profile or CapabilityClassifier().classify(self._domain)
        self._descriptor = descriptor or self._descriptor_from_profile(self._profile)
        self._browser = browser
        self._vault = vault
        self._store = recipe_store_module or recipe_store
        self._learner = learner
        self._sessions_by_account: dict = {}
        self._identity_id: Optional[str] = None
        self._last_password: Optional[str] = None
        self._last_relearn = None

    # -- descriptor ----------------------------------------------------------
    @staticmethod
    def _descriptor_from_profile(profile: SiteProfile) -> ProviderDescriptor:
        req = profile.requirements
        return ProviderDescriptor(
            provider_id=profile.domain,
            display_name=profile.domain,
            official_domain=profile.domain,
            registration_url=profile.signup_url,
            account_types=[PROVIDER_ACCOUNT_TYPE],
            has_api=False,
            has_oauth=profile.flow_type.value == "sso_only",
            browser_required=True,
            requires_email=req.email,
            requires_phone=req.phone,
            requires_captcha=req.captcha,
            requires_mfa=req.mfa,
            requires_identity_verification=req.kyc,
            requires_payment=req.payment,
            automation_policy=profile.automation_policy,
            policy_source=profile.policy_source,
            regions=["*"],
            notes=f"generic web adapter for {profile.domain}",
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    # -- browser / vault ports ----------------------------------------------
    def _cli(self):
        if self._browser is None:
            from core.browser.client import BrowserOperatorClient

            self._browser = BrowserOperatorClient()
        return self._browser

    def _resolve_identity(self, identity_id):
        if not identity_id:
            return None
        from core.identity import get_identity

        return get_identity(identity_id)

    def _resolve_value(self, source: Optional[str]) -> str:
        if not source:
            return ""
        if source == "generated_password":
            if self._last_password is None:
                self._last_password = _generate_password()
            return self._last_password
        if source.startswith("constant:"):
            return source.split(":", 1)[1]
        if source.startswith("identity."):
            ident = self._resolve_identity(self._identity_id)
            return getattr(ident, source.split(".", 1)[1], "") if ident else ""
        return ""

    def _vault_store(self, path: str, value: str) -> Optional[str]:
        writer = self._vault
        if writer is None:  # pragma: no cover - real Vault only
            from core.ai.kai_vault_client import store_secret

            writer = store_secret
        try:
            stored = writer(path, value)
        except Exception:  # noqa: BLE001 - a vault outage must not crash onboarding
            return None
        return stored or None

    # -- recipe driving ------------------------------------------------------
    def _perform(self, session_id: str, step: RecipeStep) -> dict:
        params: dict = {}
        if step.url:
            params["url"] = step.url
        if step.selector:
            params["selector"] = step.selector
        if step.value_source:
            params["value"] = self._resolve_value(step.value_source)
            if step.value_source == "generated_password":
                params["credential"] = True
        try:
            result = self._cli().perform(session_id, step.action, params)
        except Exception as exc:  # noqa: BLE001 - any operator error is drift
            return {"ok": False, "error": type(exc).__name__}
        return result if isinstance(result, dict) else {"ok": True}

    def _learn_and_pause(self, session_id: str, *, reason: str,
                         stale: bool = False) -> dict:
        if self._learner is None:
            return {"status": "learn_unavailable", "requires_human": True,
                    "action_type": "OTHER", "instructions": reason}
        draft = self._learner.learn(self._profile, session_id=session_id,
                                    browser=self._browser)
        assert_no_secret_fields(draft.model_dump())
        self._store.save_recipe(draft)
        self._last_relearn = draft
        return {"status": "drift_relearned" if stale else "draft_learned",
                "requires_human": True, "action_type": "OTHER",
                "instructions": reason, "recipe_version": draft.version}

    # -- operations ----------------------------------------------------------
    def discovery(self, **ctx) -> dict:
        return {"provider_id": self._domain, "status": "descriptor",
                "official_domain": self._domain,
                "registration_url": self._descriptor.registration_url,
                "flow_type": self._profile.flow_type.value,
                "automation_policy": self._descriptor.automation_policy.value}

    def registration(self, **ctx) -> dict:
        session_id = ctx.get("browser_session_id")
        account_id = ctx.get("account_id")
        self._identity_id = ctx.get("identity_id")
        self._last_password = None
        if not session_id:
            return {"status": "unavailable", "reason": "missing_session",
                    "requires_human": False}
        if account_id:
            self._sessions_by_account[account_id] = session_id

        published = self._store.get_published(self._domain)
        if published is None:
            return self._learn_and_pause(
                session_id,
                reason="no published recipe; learned a draft for operator review")

        for step in published.steps:
            if self._perform(session_id, step).get("ok") is False:
                self._store.mark_stale(self._domain)
                return self._learn_and_pause(
                    session_id,
                    reason="recipe drift detected; marked stale and re-learned",
                    stale=True)

        password_ref = None
        if self._last_password is not None:
            path = (vault_reference_for(self._domain, account_id) if account_id
                    else f"secrets/accounts/{self._domain}/{PROVIDER_ACCOUNT_TYPE}")
            password_ref = self._vault_store(path, self._last_password)
            self._last_password = None
        return {"status": "submitted", "requires_human": False,
                "recipe_version": published.version, "password_ref": password_ref}

    def verification(self, **ctx) -> dict:
        code = ctx.get("code")
        if not code:
            return {"status": "no_code_received", "handled_by": "mail_worker"}
        return {"status": "otp_submitted"}

    def authentication(self, **ctx) -> dict:
        return {"status": "sign_in_required"}

    def security_setup(self, **ctx) -> dict:
        account_id = ctx.get("account_id")
        reference = (vault_reference_for(self._domain, account_id)
                     if account_id else None)
        return {"status": "security_settings_reviewed", "mfa_offered": True,
                "mfa_enabled": False, "vault_reference": reference}

    def profile_setup(self, **ctx) -> dict:
        return {"status": "profile_synced"}

    def recovery(self, **ctx) -> dict:
        return {"status": "recovery_reviewed", "values_persisted": False}

    def publishing(self, **ctx):
        return NOT_APPLICABLE

    def account_status(self, **ctx) -> dict:
        return {"status": "active", "source": "recipe"}


def build_generic_adapter(domain: str, *, descriptor: Optional[ProviderDescriptor] = None,
                          browser=None, vault=None, classifier=None, learner=None,
                          reasoning=None) -> GenericWebAdapter:
    """Build the default universal adapter for *domain*."""
    classifier = classifier or CapabilityClassifier(backend=reasoning)
    profile = classifier.classify(domain)
    if learner is None:
        from core.discovery.reasoning import LocalModelReasoningBackend
        from core.site_recipes.learner import RecipeLearner

        learner = RecipeLearner(reasoning or LocalModelReasoningBackend(),
                                browser=browser)
    return GenericWebAdapter(domain, descriptor=descriptor, profile=profile,
                             browser=browser, vault=vault, learner=learner)


__all__ = ["GenericWebAdapter", "build_generic_adapter", "PROVIDER_ACCOUNT_TYPE"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generic_web_adapter.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add core/providers/generic_web.py tests/test_generic_web_adapter.py
git commit -m "feat(providers): add GenericWebAdapter with recipe/drift handling"
```

---

## Task 7: Adapter resolution in the onboarding engine

**Files:**
- Modify: `core/onboarding/engine.py:341-350` (`_h_select_provider_adapter`)
- Modify: `core/onboarding/engine.py:380-400` (`_h_start_registration`)
- Test: `tests/test_generic_web_engine.py`

- [ ] **Step 1: Write the failing test**

```python
"""Engine adapter resolution: generic fallback + requires_human pause."""

import pytest

from core.onboarding.engine import step
from core.onboarding.schema import OnboardingState
from core.providers import AutomationPolicy, register_provider
from core.providers.schema import ProviderDescriptor


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    return tmp_path


class _Account:
    account_id = "acct-1"
    vault_reference = "secrets/accounts/test/ref-1"


class ProviderPort:
    def __init__(self, adapter=None):
        self._adapter = adapter

    def get_adapter(self, provider_id):
        if self._adapter is None:
            from core.providers import AdapterNotFound

            raise AdapterNotFound(provider_id)
        return self._adapter

    def op(self, descriptor, operation, **kwargs):
        result = getattr(self._adapter, operation)(**kwargs)
        return {"not_applicable": False, "result": result}


class IdentityPort:
    def resolve(self, identity_id):
        return type("Ident", (), {"display_name": "KAI", "email": "kai@x.test",
                                  "phone": "+233200000001"})()


class AccountsPort:
    def ensure_account(self, record, descriptor, identity):
        return _Account()


class BrowserPort:
    def open_session(self, identity_id, provider_id, mission_id):
        return {"session_id": "sess-1"}


class HumanPort:
    def __init__(self):
        self.requests = []

    def request(self, action_type, mission_id, *, instructions="", provider=None):
        self.requests.append((action_type, mission_id, instructions))
        return "hact-1"

    def complete(self, action_id, *, reason=""):
        return None


class Pipeline:
    def __init__(self, *, provider, human):
        self.provider = provider
        self.human = human
        self.identity = IdentityPort()
        self.accounts = AccountsPort()
        self.browser = BrowserPort()


def _descriptor(domain="fresh.example"):
    return ProviderDescriptor(provider_id=domain, display_name=domain,
                              official_domain=domain, account_types=["web"],
                              browser_required=True, requires_email=True,
                              automation_policy=AutomationPolicy.ALLOWED,
                              policy_source="test")


def test_select_provider_adapter_falls_back_to_generic(isolated):
    descriptor = _descriptor("fresh.example")
    register_provider(descriptor)
    pipeline = Pipeline(provider=ProviderPort(adapter=None), human=HumanPort())
    record = {"provider_id": "fresh.example", "mission_id": "mis-1",
              "current_state": OnboardingState.SELECT_PROVIDER_ADAPTER.value}

    step(record, descriptor, pipeline)

    from core.providers import store
    from core.providers.generic_web import GenericWebAdapter

    adapter = store.get_adapter("fresh.example")
    assert isinstance(adapter, GenericWebAdapter)
    assert adapter.descriptor.official_domain == "fresh.example"


def test_select_provider_adapter_prefers_registered_adapter(isolated):
    from core.providers import register_adapter
    from core.providers.generic_web import GenericWebAdapter

    descriptor = _descriptor("seeded.example")
    register_provider(descriptor)
    seeded = GenericWebAdapter("seeded.example", descriptor=descriptor)
    register_adapter(seeded)
    pipeline = Pipeline(provider=ProviderPort(adapter=seeded), human=HumanPort())
    record = {"provider_id": "seeded.example", "mission_id": "mis-2",
              "current_state": OnboardingState.SELECT_PROVIDER_ADAPTER.value}

    step(record, descriptor, pipeline)

    assert record.get("errors", []) == []
    assert record["current_state"] == OnboardingState.CHECK_REQUIREMENTS.value


def test_start_registration_pauses_when_adapter_requests_human(isolated):
    from core.providers.generic_web import GenericWebAdapter

    descriptor = _descriptor("pause.example")
    register_provider(descriptor)

    class MarkingAdapter(GenericWebAdapter):
        def registration(self, **ctx):
            return {"status": "draft_learned", "requires_human": True,
                    "action_type": "OTHER", "instructions": "review recipe"}

    human = HumanPort()
    adapter = MarkingAdapter("pause.example", descriptor=descriptor)
    pipeline = Pipeline(provider=ProviderPort(adapter=adapter), human=human)
    record = {"provider_id": "pause.example", "mission_id": "mis-3",
              "identity_id": "ident-1",
              "current_state": OnboardingState.START_REGISTRATION.value}

    step(record, descriptor, pipeline)

    assert record["current_state"] == OnboardingState.PAUSED_HUMAN.value
    assert human.requests[0][0] == "OTHER"
    assert human.requests[0][2] == "review recipe"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generic_web_engine.py -q`
Expected: FAIL - `test_select_provider_adapter_falls_back_to_generic` fails because no adapter is registered (the engine returns `{"fail": "no_adapter: AdapterNotFound"}`), and `test_start_registration_pauses_when_adapter_requests_human` fails because the handler ignores `requires_human`.

- [ ] **Step 3: Write minimal implementation**

Replace `_h_select_provider_adapter` in `core/onboarding/engine.py`:

```python
def _h_select_provider_adapter(record, descriptor, p) -> dict:
    from core.providers import store as provider_store
    from core.providers.generic_web import build_generic_adapter

    try:
        adapter = p.provider.get_adapter(record["provider_id"])
    except Exception:  # noqa: BLE001 - no seeded adapter: fall back to generic web
        domain = getattr(descriptor, "official_domain", None) or record["provider_id"]
        adapter = build_generic_adapter(domain, descriptor=descriptor)
        try:
            provider_store.register_adapter(adapter)
        except provider_store.DuplicateAdapter:
            adapter = provider_store.get_adapter(record["provider_id"])
        except Exception as exc:  # noqa: BLE001
            return {"fail": f"no_adapter: {type(exc).__name__}"}
    return {"evidence": {
        "kind": "provider_adapter",
        "ref": type(adapter).__name__,
        "hash": hash_payload(descriptor.model_dump()),
        "detail": {"generic": type(adapter).__name__ == "GenericWebAdapter"},
    }}
```

Replace `_h_start_registration` in `core/onboarding/engine.py`:

```python
def _h_start_registration(record, descriptor, p) -> dict:
    identity = p.identity.resolve(record["identity_id"])
    account = p.accounts.ensure_account(record, descriptor, identity)
    record["account_id"] = account.account_id
    record["vault_reference"] = account.vault_reference
    session = p.browser.open_session(
        record["identity_id"], record["provider_id"], record["mission_id"])
    session_id = session.get("session_id") if isinstance(session, dict) else None
    record["browser_session_id"] = session_id
    response = p.provider.op(
        descriptor, "registration",
        identity_id=record["identity_id"], browser_session_id=session_id,
        mission_id=record["mission_id"], account_id=record.get("account_id"),
        objective=record.get("objective"))

    payload = response.get("result") if isinstance(response, dict) else None
    if not isinstance(payload, dict):
        payload = response if isinstance(response, dict) else {}
    if payload.get("requires_human"):
        action_type = payload.get("action_type") or "OTHER"
        action_id = p.human.request(
            action_type, record["mission_id"],
            instructions=payload.get("instructions")
            or f"Review the learned recipe for {record['provider_id']}.",
            provider=record["provider_id"])
        return {"pause": True, "action_type": action_type,
                "human_action_id": action_id}

    return {"evidence": {
        "kind": "provider_registration",
        "ref": session_id,
        "hash": hash_payload(response),
        "detail": {"not_applicable": bool(response.get("not_applicable"))}
        if isinstance(response, dict) else {},
    }}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generic_web_engine.py -q`
Expected: `3 passed`

Also confirm no regression in the existing capability suites:

Run: `.venv/bin/python -m pytest tests/test_onboarding.py tests/test_amazon_adapter.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/onboarding/engine.py tests/test_generic_web_engine.py
git commit -m "feat(onboarding): resolve GenericWebAdapter fallback and pause on requires_human"
```

---

## Task 8: Offline site fixtures + per-shape E2E

**Files:**
- Create: `tests/site_fixtures.py`
- Create: `tests/fixtures/sites/single_page.json`
- Create: `tests/fixtures/sites/multi_step.json`
- Create: `tests/fixtures/sites/sso_invite.json`
- Test: `tests/test_universal_website_e2e.py`

- [ ] **Step 1: Write the failing test**

Create the three fixture files below, `tests/site_fixtures.py`, then `tests/test_universal_website_e2e.py`.

`tests/fixtures/sites/single_page.json`:

```json
{
  "domain": "singlepage.example",
  "signup_url": "https://singlepage.example/signup",
  "start": "signup",
  "requirements": {"email": true, "email_verify": true},
  "pages": {
    "signup": {
      "url": "https://singlepage.example/signup",
      "title": "Create your account",
      "text": "Sign up for an account. Full name Email Password Create account",
      "has_password_field": true,
      "elements": [
        {"role": "textbox", "name": "Full name", "selector": "#name"},
        {"role": "textbox", "name": "Email", "selector": "#email"},
        {"role": "textbox", "name": "Password", "type": "password", "selector": "#password"}
      ],
      "on_click": "done"
    },
    "done": {
      "url": "https://singlepage.example/welcome",
      "title": "Welcome",
      "text": "Your account is ready. You are signed in. Sign out",
      "elements": [{"role": "link", "name": "Sign out", "selector": "#logout"}]
    }
  },
  "recipe": {
    "flow_type": "single_page",
    "requirements": {"email": true, "email_verify": true},
    "fields": [
      {"name": "Full name", "selector": "#name", "kind": "text", "value_source": "identity.display_name", "step": 0},
      {"name": "Email", "selector": "#email", "kind": "email", "value_source": "identity.email", "step": 0},
      {"name": "Password", "selector": "#password", "kind": "password", "value_source": "generated_password", "step": 0}
    ],
    "steps": [
      {"index": 0, "action": "navigate", "url": "https://singlepage.example/signup"},
      {"index": 1, "action": "fill", "selector": "#name", "value_source": "identity.display_name"},
      {"index": 2, "action": "fill", "selector": "#email", "value_source": "identity.email"},
      {"index": 3, "action": "fill", "selector": "#password", "value_source": "generated_password"},
      {"index": 4, "action": "click", "selector": "#submit"}
    ],
    "verification_flow": "email_link",
    "confidence": 0.9
  }
}
```

`tests/fixtures/sites/multi_step.json`:

```json
{
  "domain": "wizard.example",
  "signup_url": "https://wizard.example/register",
  "start": "step1",
  "requirements": {"email": true, "phone": true, "email_verify": true, "phone_verify": true},
  "pages": {
    "step1": {
      "url": "https://wizard.example/register",
      "title": "Step 1 of 2",
      "text": "Create an account. Email. Continue",
      "has_password_field": false,
      "elements": [{"role": "textbox", "name": "Email", "selector": "#email"}],
      "on_click": "step2"
    },
    "step2": {
      "url": "https://wizard.example/register/2",
      "title": "Step 2 of 2",
      "text": "Password Mobile number Finish",
      "has_password_field": true,
      "elements": [
        {"role": "textbox", "name": "Password", "type": "password", "selector": "#password"},
        {"role": "textbox", "name": "Mobile number", "selector": "#phone"}
      ],
      "on_click": "done"
    },
    "done": {
      "url": "https://wizard.example/welcome",
      "title": "All set",
      "text": "Account created. You are signed in. Sign out",
      "elements": [{"role": "link", "name": "Sign out", "selector": "#logout"}]
    }
  },
  "recipe": {
    "flow_type": "multi_step",
    "requirements": {"email": true, "phone": true, "email_verify": true, "phone_verify": true},
    "fields": [
      {"name": "Email", "selector": "#email", "kind": "email", "value_source": "identity.email", "step": 0},
      {"name": "Password", "selector": "#password", "kind": "password", "value_source": "generated_password", "step": 1},
      {"name": "Mobile number", "selector": "#phone", "kind": "phone", "value_source": "identity.phone", "step": 1}
    ],
    "steps": [
      {"index": 0, "action": "navigate", "url": "https://wizard.example/register"},
      {"index": 1, "action": "fill", "selector": "#email", "value_source": "identity.email"},
      {"index": 2, "action": "click", "selector": "#next"},
      {"index": 3, "action": "fill", "selector": "#password", "value_source": "generated_password"},
      {"index": 4, "action": "fill", "selector": "#phone", "value_source": "identity.phone"},
      {"index": 5, "action": "click", "selector": "#finish"}
    ],
    "verification_flow": "email_link+otp",
    "confidence": 0.85
  }
}
```

`tests/fixtures/sites/sso_invite.json`:

```json
{
  "domain": "invite.example",
  "signup_url": "https://invite.example/invite",
  "start": "invite",
  "requirements": {"email": true, "email_verify": true},
  "pages": {
    "invite": {
      "url": "https://invite.example/invite",
      "title": "Request an invitation",
      "text": "This product is invite-only. Enter the email your invitation was sent to. Continue with email.",
      "has_password_field": false,
      "elements": [{"role": "textbox", "name": "Invite email", "selector": "#invite-email"}],
      "on_click": "done"
    },
    "done": {
      "url": "https://invite.example/welcome",
      "title": "Welcome",
      "text": "Your invitation was accepted. You are signed in. Sign out",
      "elements": [{"role": "link", "name": "Sign out", "selector": "#logout"}]
    }
  },
  "recipe": {
    "flow_type": "sso_only",
    "requirements": {"email": true, "email_verify": true},
    "fields": [
      {"name": "Invite email", "selector": "#invite-email", "kind": "email", "value_source": "identity.email", "step": 0}
    ],
    "steps": [
      {"index": 0, "action": "navigate", "url": "https://invite.example/invite"},
      {"index": 1, "action": "fill", "selector": "#invite-email", "value_source": "identity.email"},
      {"index": 2, "action": "click", "selector": "#continue"}
    ],
    "verification_flow": "email_link",
    "confidence": 0.8
  }
}
```

`tests/site_fixtures.py`:

```python
"""Offline site-shape fixtures + a fake browser for universal-web tests.

Each JSON fixture describes a distinct signup shape as a set of named pages
(URL, accessible text, element refs, the page reached on click) plus a seeded
Site Recipe. ``FakeSiteBrowser`` replays those pages; nothing touches a network.
"""

from __future__ import annotations

import json
from pathlib import Path

SITES_DIR = Path(__file__).parent / "fixtures" / "sites"


def load_site(name: str) -> dict:
    return json.loads((SITES_DIR / f"{name}.json").read_text())


class FakeVault:
    def __init__(self):
        self.writes = {}

    def __call__(self, path, value):
        self.writes[path] = value
        return path


class FakeSiteBrowser:
    """Deterministic, resumable, offline stand-in for the CT110 operator."""

    def __init__(self, fixture: dict, *, resume_after: int = 1):
        self.fixture = fixture
        self.sessions = {}
        self.calls = []
        self.failing_selectors = set()
        self.resume_after = resume_after
        self._seq = 0

    def create_profile(self, identity_id, provider_id):
        self.calls.append(("create_profile", identity_id, provider_id))
        return {"profile_id": f"prof-{identity_id}-{provider_id}"}

    def open_session(self, identity_id, provider_id, mission_id=None, headless=None):
        self._seq += 1
        sid = f"sess-{self.fixture['domain']}-{self._seq}"
        start = self.fixture["pages"][self.fixture["start"]]
        self.sessions[sid] = {"identity_id": identity_id, "provider_id": provider_id,
                              "mission_id": mission_id, "page": start,
                              "fills": [], "clicks": [], "resumed": 0}
        return {"session_id": sid, "identity_id": identity_id,
                "provider_id": provider_id, "mission_id": mission_id}

    @staticmethod
    def _snap(page):
        return {"url": page["url"], "title": page["title"],
                "text": page.get("text", ""), "elements": page.get("elements", []),
                "has_password_field": page.get("has_password_field", False),
                "untrusted": True}

    def _page_for_url(self, url):
        for page in self.fixture["pages"].values():
            if page["url"] == url:
                return page
        return self.fixture["pages"][self.fixture["start"]]

    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        st = self.sessions.setdefault(session_id, {
            "page": self.fixture["pages"][self.fixture["start"]],
            "fills": [], "clicks": [], "resumed": 0})
        self.calls.append((op, params.get("selector") or params.get("url")))
        if op == "navigate":
            st["page"] = self._page_for_url(params.get("url"))
            return {"op": op, "snapshot": self._snap(st["page"])}
        if op == "inspect":
            return {"op": op, "snapshot": self._snap(st["page"])}
        if op in ("fill", "click"):
            sel = params.get("selector")
            if sel in self.failing_selectors:
                return {"op": op, "ok": False, "error": "selector_not_found"}
            if op == "fill":
                st["fills"].append(sel)
            else:
                st["clicks"].append(sel)
                nxt = st["page"].get("on_click")
                if nxt:
                    st["page"] = self.fixture["pages"][nxt]
            return {"op": op, "ok": True}
        return {"op": op, "ok": True}

    def pause_for_human(self, session_id, reason="", action_required="", **kwargs):
        st = self.sessions.setdefault(session_id, {})
        return {"takeover_id": f"take-{session_id}", "session_id": session_id,
                "mission_id": st.get("mission_id"), "provider_id": st.get("provider_id"),
                "action_required": action_required, "instructions": reason,
                "no_vnc_url": None}

    def resume(self, session_id):
        st = self.sessions.setdefault(session_id, {"resumed": 0})
        st["resumed"] = st.get("resumed", 0) + 1
        return {"resumed": st["resumed"] >= self.resume_after,
                "takeover_id": f"take-{session_id}"}

    def end_session(self, session_id):
        return {"session_id": session_id, "status": "ended"}


def descriptor_from_site(fixture: dict):
    from core.providers import AutomationPolicy, ProviderDescriptor

    req = fixture["requirements"]
    return ProviderDescriptor(
        provider_id=fixture["domain"], display_name=fixture["domain"],
        official_domain=fixture["domain"], registration_url=fixture["signup_url"],
        account_types=["web"], browser_required=True,
        requires_email=req.get("email", False),
        requires_phone=req.get("phone", False),
        requires_captcha=req.get("captcha", False),
        requires_mfa=req.get("mfa", False),
        requires_identity_verification=req.get("kyc", False),
        requires_payment=req.get("payment", False),
        automation_policy=AutomationPolicy.ALLOWED,
        policy_source="fixture: operator-confirmed offline simulation",
        regions=["*"])


def recipe_from_site(fixture: dict):
    from core.site_recipes.schema import RecipeSource, RecipeStatus, SiteRecipe

    data = dict(fixture["recipe"])
    confidence = float(data.pop("confidence", 0.9))
    return SiteRecipe(domain=fixture["domain"], signup_url=fixture["signup_url"],
                      status=RecipeStatus.published, source=RecipeSource.seeded,
                      confidence=confidence, **data)


def make_adapter(fixture: dict, *, browser, vault):
    from core.discovery.reasoning import FixtureReasoningBackend
    from core.providers.generic_web import GenericWebAdapter
    from core.site_recipes.learner import RecipeLearner
    from core.site_recipes.schema import FlowType, SiteProfile, SiteRequirements

    recipe = fixture["recipe"]
    profile = SiteProfile(domain=fixture["domain"],
                          signup_url=fixture["signup_url"],
                          flow_type=FlowType(recipe["flow_type"]),
                          requirements=SiteRequirements(**recipe["requirements"]))
    backend = FixtureReasoningBackend(recipes={fixture["domain"]: recipe})
    learner = RecipeLearner(backend, browser=browser)
    return GenericWebAdapter(fixture["domain"], descriptor=descriptor_from_site(fixture),
                             profile=profile, browser=browser, vault=vault,
                             learner=learner)
```

`tests/test_universal_website_e2e.py`:

```python
"""Universal website registration - offline E2E per signup shape."""

import pytest

from core.onboarding.manager import default_pipeline, start_onboarding
from core.onboarding.schema import OnboardingState, OnboardingStatus
from core.providers import register_adapter, register_provider
from core.site_recipes import store as recipe_store
from tests.site_fixtures import (
    FakeSiteBrowser,
    FakeVault,
    descriptor_from_site,
    load_site,
    make_adapter,
    recipe_from_site,
)

SHAPES = ["single_page", "multi_step", "sso_invite"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    from core.notify import human_action

    monkeypatch.setattr(human_action, "_send_telegram", lambda text: None)
    return tmp_path


@pytest.fixture
def missions(monkeypatch):
    created = []

    def _create(objective, tasks=None, **kwargs):
        mid = f"mis-e2e-{len(created) + 1}"
        created.append(mid)
        return {"id": mid}

    monkeypatch.setattr("core.kai_missions.create_mission", _create)
    monkeypatch.setattr("core.kai_missions.add_checkpoint",
                        lambda *a, **k: {"id": "mis"})
    return created


def _identity(email="kai-e2e@example.invalid", phone="+233200000009"):
    from core.identity import IdentityCreate, create_identity, set_default_identity

    ident = create_identity(IdentityCreate(display_name="KAI E2E", email=email,
                                           phone=phone))
    set_default_identity(ident.identity_id)
    return ident


def _pipeline(browser):
    def _email_ok(record, descriptor):
        return {"verified": True, "email_id": "em-1", "message_id": "msg-1"}

    def _sms_ok(record, descriptor):
        return {"otp_present": True, "verified": True, "message_id": "sms-1"}

    return default_pipeline(client=browser, email_verifier=_email_ok,
                            sms_consumer=_sms_ok)


@pytest.mark.parametrize("shape", SHAPES)
def test_offline_shape_reaches_completed(shape, isolated, missions):
    fixture = load_site(shape)
    browser = FakeSiteBrowser(fixture)
    vault = FakeVault()
    descriptor = descriptor_from_site(fixture)
    register_provider(descriptor)
    register_adapter(make_adapter(fixture, browser=browser, vault=vault))
    recipe_store.save_recipe(recipe_from_site(fixture))
    recipe_store.publish_recipe(fixture["domain"])

    identity = _identity()
    session = start_onboarding(fixture["domain"], identity_id=identity.identity_id,
                               mission_id=f"mis-e2e-{shape}",
                               pipeline=_pipeline(browser))

    assert session.status == OnboardingStatus.COMPLETED, session.errors
    assert session.current_state == OnboardingState.COMPLETED

    from core.accounts import VerificationStatus, get_account

    account = get_account(session.account_id)
    assert account.provider == fixture["domain"]
    assert account.verification_status == VerificationStatus.VERIFIED
    assert session.evidence
    # recipe drove the flow through the browser operator
    assert ("navigate", fixture["signup_url"]) in [
        (op, t) for op, t in browser.calls if op == "navigate"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_universal_website_e2e.py -q`
Expected: FAIL - initially `ImportError: No module named 'tests.site_fixtures'` (or, once the support module exists, the run pauses at `START_REGISTRATION` because Task 7's `requires_human` pause is not yet applied). Implement Task 7 first if running out of order; after Task 7 the tests should pass.

- [ ] **Step 3: Write minimal implementation**

The `GenericWebAdapter` and engine changes from Tasks 6 and 7 are the entire implementation for this task; the new code here is the fixture data and the offline test doubles in `tests/site_fixtures.py`. No production code changes are needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_universal_website_e2e.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/site_fixtures.py tests/fixtures/sites/ tests/test_universal_website_e2e.py
git commit -m "test(registration): offline E2E for single-page, multi-step, sso/invite shapes"
```

---

## Task 9: Drift detection through the engine

**Files:**
- Test: `tests/test_site_recipe_drift.py`
- (No new production code: Task 6 drift handling + Task 7 pause wiring implement it.)

- [ ] **Step 1: Write the failing test**

```python
"""Drift: a recipe selector fails -> stale, re-learn, engine pauses."""

import pytest

from core.onboarding.manager import default_pipeline, start_onboarding
from core.onboarding.schema import OnboardingState, OnboardingStatus
from core.providers import register_adapter, register_provider
from core.site_recipes import store as recipe_store
from tests.site_fixtures import (
    FakeSiteBrowser,
    FakeVault,
    descriptor_from_site,
    load_site,
    make_adapter,
    recipe_from_site,
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    from core.notify import human_action

    monkeypatch.setattr(human_action, "_send_telegram", lambda text: None)
    return tmp_path


@pytest.fixture
def missions(monkeypatch):
    monkeypatch.setattr("core.kai_missions.create_mission",
                        lambda objective, tasks=None, **k: {"id": "mis-drift"})
    monkeypatch.setattr("core.kai_missions.add_checkpoint",
                        lambda *a, **k: {"id": "mis-drift"})


def _identity():
    from core.identity import IdentityCreate, create_identity, set_default_identity

    ident = create_identity(IdentityCreate(display_name="KAI Drift",
                                           email="drift@example.invalid",
                                           phone="+233200000010"))
    set_default_identity(ident.identity_id)
    return ident


def test_drift_marks_stale_relearns_and_pauses(isolated, missions):
    fixture = load_site("single_page")
    browser = FakeSiteBrowser(fixture)
    # Break a published selector so the recipe no longer resolves.
    browser.failing_selectors = {"#password"}
    vault = FakeVault()
    descriptor = descriptor_from_site(fixture)
    register_provider(descriptor)
    register_adapter(make_adapter(fixture, browser=browser, vault=vault))
    recipe_store.save_recipe(recipe_from_site(fixture))
    recipe_store.publish_recipe(fixture["domain"])

    identity = _identity()
    session = start_onboarding(fixture["domain"], identity_id=identity.identity_id,
                               mission_id="mis-drift",
                               pipeline=default_pipeline(client=browser,
                                                         email_verifier=lambda r, d: {"verified": True},
                                                         sms_consumer=lambda r, d: {"verified": True}))

    # The engine paused for a human because the learned recipe needs review.
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.pre_pause_state == OnboardingState.START_REGISTRATION.value
    assert session.human_action_type == "OTHER"

    versions = recipe_store.read_versions(fixture["domain"])
    assert any(v["status"] == "stale" for v in versions)
    drafts = [v for v in versions if v["status"] == "draft" and v["source"] == "learned"]
    assert drafts, "a re-learned draft must be stored"
    # re-learning inspected the live page
    assert ("inspect", None) in browser.calls or any(op == "inspect" for op, _ in browser.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_site_recipe_drift.py -q`
Expected: FAIL before Task 6/7 land (no `stale` version and no pause). After Tasks 6-7 are complete this should pass; if it fails at that point, inspect `recipe_store.read_versions` for a missing `stale` row.

- [ ] **Step 3: Write minimal implementation**

No new production code. The behavior is provided by `GenericWebAdapter.registration` (Task 6: on any step returning `{"ok": False}`, call `self._store.mark_stale(...)` then `_learn_and_pause(..., stale=True)`) and `_h_start_registration` (Task 7: pause when the adapter payload has `requires_human=True`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_site_recipe_drift.py -q`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_site_recipe_drift.py
git commit -m "test(registration): drift marks recipe stale, relearns and pauses"
```

---

## Task 10: Command Center recipe viewer API + panel

**Files:**
- Modify: `core/cc_account_routes.py` (append shaping helpers + routes before `__all__`)
- Modify: `core/kai/command_center.html` (nav item, panel section, `PANEL_LOADERS`, `loadSiteRecipes`)
- Test: `tests/test_site_recipes_api.py`

- [ ] **Step 1: Write the failing test**

```python
"""CC site-recipe viewer API - auth, shaping, publish, leak-prevention."""

import pytest
from fastapi.testclient import TestClient

OP = {"X-Kai-User": "cc@kai", "X-Kai-User-Id": "cc"}


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


def _seed_recipe(domain="api.example", *, published=True):
    from core.site_recipes import store
    from core.site_recipes.schema import (
        RecipeStatus,
        RecipeStep,
        SiteRecipe,
        SiteRequirements,
    )

    recipe = SiteRecipe(
        domain=domain, signup_url=f"https://{domain}/signup",
        status=RecipeStatus.draft, requirements=SiteRequirements(email=True),
        steps=[
            RecipeStep(index=0, action="navigate", url=f"https://{domain}/signup"),
            RecipeStep(index=1, action="fill", selector="#email",
                       value_source="identity.email"),
        ])
    store.save_recipe(recipe)
    if published:
        store.publish_recipe(domain)
    return domain


def test_site_recipes_require_operator(client):
    assert client.get("/api/site-recipes").status_code == 401
    assert client.get("/api/site-recipes/api.example").status_code == 401
    assert client.post("/api/site-recipes/api.example/publish").status_code == 401


def test_list_and_detail(client):
    _seed_recipe("api.example")
    listing = client.get("/api/site-recipes", headers=OP)
    assert listing.status_code == 200
    body = listing.json()
    assert body["ok"] is True and body["count"] >= 1
    row = next(r for r in body["recipes"] if r["domain"] == "api.example")
    assert row["status"] == "published"
    assert row["requirements"]["email"] is True

    detail = client.get("/api/site-recipes/api.example", headers=OP)
    assert detail.status_code == 200
    recipe = detail.json()["recipe"]
    assert recipe["steps"][1]["selector"] == "#email"
    assert recipe["steps"][1]["value_source"] == "identity.email"


def test_detail_404_for_unknown(client):
    assert client.get("/api/site-recipes/nope.example", headers=OP).status_code == 404


def test_publish_promotes_draft(client):
    _seed_recipe("draft.example", published=False)
    r = client.post("/api/site-recipes/draft.example/publish", headers=OP)
    assert r.status_code == 200
    assert r.json()["recipe"]["status"] == "published"


def test_publish_404_for_unknown(client):
    assert client.post("/api/site-recipes/nope.example/publish", headers=OP).status_code == 404


def test_no_secret_leak_across_recipe_endpoints(client):
    _seed_recipe("leak.example")
    calls = [
        ("get", "/api/site-recipes"),
        ("get", "/api/site-recipes/leak.example"),
    ]
    for method, path in calls:
        resp = getattr(client, method)(path, headers=OP)
        assert resp.status_code == 200
        data = resp.json()
        from core.secret_guard import find_secret_fields

        assert find_secret_fields(data) == [], f"secret-looking fields in {path}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_api.py -q`
Expected: FAIL - the routes return 404 (not registered), so `test_list_and_detail` and the publish tests fail.

- [ ] **Step 3: Write minimal implementation**

Append to `core/cc_account_routes.py` (immediately before the existing `__all__ = ["cc_account_router"]` at the end):

```python
# ---------------------------------------------------------------------------
# site-recipe viewer routes (universal website registration)
# ---------------------------------------------------------------------------


class RecipePublish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Optional[int] = Field(default=None, ge=1)


def _recipe_view(recipe) -> dict:
    """Compact, secret-free recipe summary. Selectors/URLs/symbolic sources only."""
    return {
        "domain": recipe.domain,
        "signup_url": recipe.signup_url,
        "flow_type": recipe.flow_type.value,
        "status": recipe.status.value,
        "source": recipe.source.value,
        "version": recipe.version,
        "confidence": recipe.confidence,
        "requirements": recipe.requirements.model_dump(),
        "verification_flow": recipe.verification_flow,
        "last_verified_at": recipe.last_verified_at,
        "field_count": len(recipe.fields),
        "step_count": len(recipe.steps),
    }


def _recipe_detail(recipe) -> dict:
    detail = _recipe_view(recipe)
    detail["fields"] = [f.model_dump() for f in recipe.fields]
    detail["steps"] = [s.model_dump() for s in recipe.steps]
    detail["evidence_ref"] = recipe.evidence_ref
    detail["created_at"] = recipe.created_at
    detail["updated_at"] = recipe.updated_at
    return detail


@cc_account_router.get("/api/site-recipes")
def site_recipes_list(_: None = Depends(_req_op)):
    """List the latest stored Site Recipe per domain (reviewable signup flows)."""
    from core.site_recipes import store as recipe_store

    try:
        recipes = recipe_store.list_recipes()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"recipe store unavailable: {type(exc).__name__}")
    return {"ok": True, "recipes": [_recipe_view(r) for r in recipes],
            "count": len(recipes)}


@cc_account_router.get("/api/site-recipes/{domain}")
def site_recipes_detail(domain: str, _: None = Depends(_req_op)):
    """Full recipe detail: ordered steps and fields (still secret-free)."""
    from core.site_recipes import store as recipe_store

    recipe = recipe_store.get_recipe(domain)
    if recipe is None:
        raise HTTPException(status_code=404, detail="site recipe not found")
    return {"ok": True, "recipe": _recipe_detail(recipe)}


@cc_account_router.post("/api/site-recipes/{domain}/publish")
def site_recipes_publish(domain: str, body: Optional[RecipePublish] = None,
                         _: None = Depends(_req_op)):
    """Promote a recipe version to published (operator action, audited shape)."""
    from core.site_recipes import store as recipe_store

    if recipe_store.get_recipe(domain) is None:
        raise HTTPException(status_code=404, detail="site recipe not found")
    version = body.version if body else None
    recipe = recipe_store.publish_recipe(domain, version=version)
    return {"ok": True, "recipe": _recipe_detail(recipe)}
```

Modify `core/kai/command_center.html` in four places.

1. Add a nav item immediately after the Providers nav item (find the `<a class="nav-item" href="#providers" ...>` line and insert after it):

```html
      <a class="nav-item" href="#site-recipes" data-hash="site-recipes"><span class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v4H4zM4 10h16v10H4z"/><path d="M8 14h8M8 17h5"/></svg></span><span class="label">Site Recipes</span></a>
```

2. Add a panel section immediately after `<section class="panel" id="panel-providers">...`:

```html
      <section class="panel" id="panel-site-recipes"><div id="site-recipes-content"><span class="loading">loading…</span></div></section>
```

3. Add the loader to `PANEL_LOADERS` (append `'site-recipes':loadSiteRecipes` before the closing `};`):

```js
'site-recipes':loadSiteRecipes
```

4. Add the loader functions near `loadProviders`:

```js
async function loadSiteRecipes(){
  const el=$('site-recipes-content');if(!el)return;el.innerHTML='<span class="loading">loading…</span>';
  try{
    const d=await api('/api/site-recipes');
    const list=d.recipes||[];
    let html='<div class="spread" style="margin-bottom:10px"><span style="font-size:0.68rem;color:var(--fg-muted);font-family:var(--font-mono);text-transform:uppercase;letter-spacing:0.5px">Site Recipes \u00b7 reviewable signup flows</span><button class="btn btn-xs" onclick="loadSiteRecipes()">Refresh</button></div>';
    html+='<div class="grid">';
    if(!list.length)html+='<div class="card"><p class="empty">No site recipes learned yet.</p></div>';
    for(const r of list){
      const req=r.requirements||{};const chips=[];
      if(req.email)chips.push('email');if(req.phone)chips.push('phone');if(req.captcha)chips.push('captcha');if(req.mfa)chips.push('mfa');if(req.kyc)chips.push('KYC');if(req.payment)chips.push('payment');
      html+='<div class="card"><div class="spread"><h3 style="margin:0;font-size:0.8rem">'+esc(r.domain)+'</h3>'+uarPolicyBadge(String(r.status||'').toUpperCase())+'</div>'
        +'<div style="font-size:0.62rem;color:var(--fg-muted);font-family:var(--font-mono);margin:2px 0 6px">'+esc(r.flow_type)+' \u00b7 v'+esc(r.version)+' \u00b7 '+esc(r.source)+'</div>'
        +'<div style="font-size:0.64rem;margin-bottom:6px"><b>Requires:</b> '+(chips.length?chips.map(c=>'<span class="badge info" style="margin-right:3px">'+esc(c)+'</span>').join(''):'<span class="field-hint">nothing beyond browser</span>')+'</div>'
        +'<div style="font-size:0.62rem;margin-bottom:4px"><b>Confidence:</b> '+esc(r.confidence)+' \u00b7 '+esc(r.step_count)+' steps \u00b7 '+esc(r.field_count)+' fields</div>'
        +'<button class="btn btn-xs" data-domain="'+attr(r.domain)+'" onclick="srDetail(this.dataset.domain)">Inspect</button> '
        +(r.status!=='published'?'<button class="btn btn-xs btn-accent" data-domain="'+attr(r.domain)+'" onclick="srPublish(this.dataset.domain)">Publish</button>':'')
        +'</div>';
    }
    html+='</div><div id="sr-detail"></div>';
    el.innerHTML=html;
  }catch(e){el.innerHTML='<span class="error-text">'+esc(e.message)+'</span>';}
}
async function srDetail(domain){
  const el=$('sr-detail');if(!el)return;el.innerHTML='<span class="loading">loading…</span>';
  try{const d=await api('/api/site-recipes/'+encodeURIComponent(domain));const r=d.recipe||{};
    let h='<h4 style="font-size:0.75rem;margin-top:10px">'+esc(r.domain)+' \u00b7 steps</h4>';
    h+='<div class="table-wrap"><table><thead><tr><th>#</th><th>Action</th><th>Target</th><th>Value source</th></tr></thead><tbody>';
    for(const s of (r.steps||[]))h+='<tr><td>'+esc(s.index)+'</td><td>'+esc(s.action)+'</td><td style="font-size:0.6rem">'+esc(s.selector||s.url||'')+'</td><td style="font-size:0.6rem">'+esc(s.value_source||'')+'</td></tr>';
    h+='</tbody></table></div>';
    el.innerHTML=h;
  }catch(e){el.innerHTML='<span class="error-text">'+esc(e.message)+'</span>';}
}
async function srPublish(domain){
  try{await api('/api/site-recipes/'+encodeURIComponent(domain)+'/publish',{method:'POST',body:'{}'});toast('recipe published');loadSiteRecipes();}
  catch(e){toast('error: '+e.message,'err');}
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_api.py -q`
Expected: `6 passed`

Sanity-check the panel loads without JS errors in the Command Center shell (open `#site-recipes`; an empty "No site recipes learned yet." card is expected on a clean store).

- [ ] **Step 5: Commit**

```bash
git add core/cc_account_routes.py core/kai/command_center.html tests/test_site_recipes_api.py
git commit -m "feat(command-center): add site-recipe viewer API and panel"
```

---

## Task 11: Final verification

**Files:**
- No file changes (verification only).

- [ ] **Step 1: Run the new capability suite**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_schema.py tests/test_site_recipes_store.py tests/test_discovery_reasoning.py tests/test_discovery_classifier.py tests/test_site_recipes_learner.py tests/test_generic_web_adapter.py tests/test_generic_web_engine.py tests/test_universal_website_e2e.py tests/test_site_recipe_drift.py tests/test_site_recipes_api.py -q`
Expected: all pass.

- [ ] **Step 2: Run the existing capability suites (no regression)**

Run: `.venv/bin/python -m pytest tests/test_provider_registry.py tests/test_provider_gate.py tests/test_onboarding.py tests/test_amazon_adapter.py tests/test_account_registration_api.py -q`
Expected: all pass.

- [ ] **Step 3: Coverage for the new modules (target 80%+)**

Run: `.venv/bin/python -m pytest tests/test_site_recipes_schema.py tests/test_site_recipes_store.py tests/test_discovery_classifier.py tests/test_site_recipes_learner.py tests/test_generic_web_adapter.py --cov=core.site_recipes --cov=core.discovery --cov=core.providers.generic_web --cov-report=term-missing`
Expected: `TOTAL` coverage >= 80%.

- [ ] **Step 4: Confirm the safety constraints by inspection**

```bash
grep -rn "generated_password\|value_source" core/site_recipes/ core/providers/generic_web.py | head
grep -rn "captcha" core/providers/generic_web.py
```

Expected: `generated_password` appears only as a symbolic value *source*; no literal secret is stored; no CAPTCHA/anti-bot bypass logic exists.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs(registration): universal website registration plan verified"
```

(If nothing is staged, skip the commit; verification produces no code changes.)

---

## Self-Review

**1. Spec coverage**

| Spec requirement | Task |
|---|---|
| `SiteRecipe` schema (domain, signup_url, flow_type, fields, requirements, steps, verification_flow, confidence, source, status, version, last_verified_at, evidence_ref; no secrets) | Task 1 |
| `SiteProfile` + enums `FlowType`, `RecipeStatus`, `FieldSpec`, requirements | Task 1 |
| `SiteRecipeStore` versioned CRUD, `draft->published`, `list`/`stale` on `memory/site_recipes/<domain>.json` | Task 2 |
| `CapabilityClassifier`: URL/domain -> `SiteProfile`, heuristics + reasoning hook, policy UNKNOWN | Task 4 |
| `ReasoningBackend` protocol + `FixtureReasoningBackend` + `LocalModelReasoningBackend` | Task 3 |
| `GenericWebAdapter`: descriptor from profile; published recipe else learn draft; operations -> browser operator; drift -> stale + re-learn + pause; no-secrets guard | Task 6 |
| Adapter resolution `SELECT_PROVIDER_ADAPTER`: exact/seeded first else `GenericWebAdapter(domain)` | Task 7 |
| Offline fixtures for >=3 shapes + per-shape E2E reaching COMPLETED | Task 8 |
| Drift detection test (stale + re-learn + pause) | Task 9 |
| CC recipe viewer API + panel (`GET`, `GET {domain}`, `POST publish`) + leak prevention | Task 10 |
| Final verification | Task 11 |
| Policy UNKNOWN -> human confirmation | Tasks 4, 7 (gate pause) |
| No CAPTCHA/anti-bot bypass, no live runs | Constraints; Tasks 6, 8, 11 |

Open decisions from the spec that are deliberately **out of scope** for this plan (documented, not implemented): the initial seeded recipe set, auto-publish confidence thresholds, and which CC surface hosts the editor (this plan adds a viewer + explicit publish, not an editor). These are additive follow-ups.

**2. Placeholder scan**

No `TBD`, `TODO`, "implement later", "add appropriate error handling", or "similar to Task N". Every code step shows complete, runnable code. Every command has an explicit expected result.

**3. Type consistency**

- `SiteRecipe`/`SiteProfile` field names are identical across schema, store, learner, adapter, engine and CC shaping.
- `RecipeStatus` values (`draft`/`published`/`stale`) are used consistently; `get_recipe(status=...)` and `mark_stale` agree.
- `GenericWebAdapter.registration` returns the keys the engine reads: `status`, `requires_human`, `action_type`, `instructions`, `recipe_version`, `password_ref`.
- `build_generic_adapter(domain, *, descriptor, browser, vault, classifier, learner, reasoning)` matches both its call site in `engine._h_select_provider_adapter` and its tests.
- `ReasoningBackend.propose_profile` / `propose_recipe` signatures match the `FixtureReasoningBackend` and `LocalModelReasoningBackend` implementations and all callers.
- `tests/site_fixtures.py` exports (`load_site`, `FakeSiteBrowser`, `FakeVault`, `descriptor_from_site`, `recipe_from_site`, `make_adapter`) match the imports in all three test modules that use them.

