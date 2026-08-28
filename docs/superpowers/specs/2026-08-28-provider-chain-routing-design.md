# Provider Chain Routing UI — Spec

## Context

Kai's `core/ai/ai_router.py` routes every task to a provider chain defined in `ROLE_PROVIDERS` (and related dicts). The ordering is hardcoded in Python comments with a rich history of operator decisions. There is no UI to view or change this order — operators must edit the source file directly. This spec adds a dashboard UI to view, reorder, and persist provider chains for all module types.

## Decisions

- **UI location**: New top-level tab `Provider Chains` in the Kai dashboard plugin (`src/ai-orchestrator-plugin/src/index.ts`)
- **Module scope**: All modules in `ROLE_PROVIDERS`, `LAW_TUTOR_ROLE_PROVIDERS`, and `JURIS_KAI_ROLE_PROVIDERS` (22 modules total)
- **Chain depth**: Drag-to-reorder list — show the full chain per module, not a fixed 3-slot limit
- **Persistence**: File-based JSON at `memory/provider_order.json`, read by `ai_router.py` at startup
- **Backend API**: FastAPI routes in `core/api.py` for read/write/reset operations
- **Drag-and-drop**: HTML5 native Drag-and-Drop API — no external library dependency

---

## File Layout

```
ai-orchestrator/
  core/
    api.py                        # new provider-chains routes
    ai/
      ai_router.py               # load provider_order.json on init
  memory/
    provider_order.json          # { schema_version, chains: { module: [providers] } }
  docs/superpowers/specs/
    2026-08-28-provider-chain-routing-design.md
src/ai-orchestrator-plugin/src/
  index.ts                       # new tab + TABS entry
```

---

## Data Model

`memory/provider_order.json` schema:

```json
{
  "schema_version": 1,
  "chains": {
    "coding": ["free_coding", "claude", "omniroute_deepseek_coding", "omniroute", "gpuai_minimax"],
    "planning": ["local", "gemini", "geminix", "openrouter", "deepseek_native_pro", ...],
    ...
  }
}
```

- `schema_version` integer — bumped on structural changes for forward/backward compatibility
- `chains` object — one key per module name, value is a list of provider name strings
- If `memory/provider_order.json` does not exist, `ai_router.py` uses `ROLE_PROVIDERS` defaults
- A module key absent from the file → router falls back to `ROLE_PROVIDERS` defaults for that module
- Unknown provider names in a chain → rendered as warning badges in UI; router skips them at runtime

---

## Backend API

Base path: `/api/provider-chains`

### GET /api/provider-chains

Returns the full chains object merged with defaults.

Response `200`:
```json
{
  "schema_version": 1,
  "chains": { "coding": [...], "planning": [...], ... },
  "source": { "coding": "file", "planning": "default" }
}
```
`source` field indicates whether each chain came from the file or is a default.

### GET /api/provider-chains/{module}

Returns one module's chain.

Response `200`: `{ "module": "coding", "chain": [...], "source": "file|default" }`
Response `404`: `{ "error": "unknown module" }`

### PUT /api/provider-chains/{module}

Replace the chain for a single module.

Request body: `{ "chain": ["provider_a", "provider_b", ...] }`

Validation:
- `chain` must be an array of strings with at least 1 element
- Each string must match a registered provider name in `core.ai_provider.PROVIDERS` — unknown names return `400` with `{ "error": "unknown providers", "unknown": ["name1"] }`

Response `200`: `{ "module": "coding", "chain": [...], "source": "file" }`

### PUT /api/provider-chains

Replace entire file content.

Request body: `{ "chains": { "coding": [...], ... } }`

Response `200`: full merged object (same shape as GET)

### POST /api/provider-chains/reset

Delete `memory/provider_order.json`, resetting all chains to `ROLE_PROVIDERS` defaults.

Response `200`: `{ "reset": true, "message": "All chains reset to defaults" }`

---

## ai_router.py Changes

On module import / `delegate()` call (not per-request):

```python
import os
from core.memory import load, save

PROVIDER_ORDER_FILE = "provider_order.json"

def _load_chain_overrides() -> dict[str, list[str]]:
    path = os.path.join(os.environ.get("KAI_MEMORY_DIR", "memory"), PROVIDER_ORDER_FILE)
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
        return data.get("chains", {})
    except Exception:
        return {}

# Loaded once at module import time
_CHAIN_OVERRIDES = _load_chain_overrides()

def _chain_for_task_type(task_type: str) -> list[str]:
    overrides = _chain_overrides.get(task_type)
    if overrides:
        return overrides
    return ROLE_PROVIDERS.get(task_type, ["claude"])
```

The router calls `_chain_for_task_type(task_type)` instead of reading `ROLE_PROVIDERS` directly. No hot-reload — changes take effect on service restart.

---

## Frontend UI

### Tab Registration

In `src/ai-orchestrator-plugin/src/index.ts`:

```typescript
const TABS = [
  // ... existing tabs
  { id: 'provider-chains', label: 'Provider Chains' },
]
```

Add `'provider-chains'` to the tab bar, mapped to `renderProviderChains(api)` in `renderTab()`.

### Module

New exported function `renderProviderChains(api: PluginAPI): Promise<HTMLElement>`.

### Layout

```
┌─ Provider Chains ─────────────────────────────────────────────┐
│                                                                │
│  ┌─ coding ──────────────────────────────────────────────┐   │
│  │  [free_coding ●] [claude ●] [omniroute_deepseek ●]  ⋮│   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌─ planning ───────────────────────────────────────────┐   │
│  │  [local ●] [gemini ●] [geminix ●] [openrouter ○]   ⋮│   │
│  └──────────────────────────────────────────────────────┘   │
│  ...                                                         │
└──────────────────────────────────────────────────────────────┘
```

- **Accordion per module** — header shows module name + provider count badge + health indicator dot
- **Expanded state** — draggable chip list, one chip per provider
- **Chip** — rounded pill showing provider name + colored health dot (green = connected, gray = unknown, red = disabled)
- **Drag handle** — chip is draggable via HTML5 DnD; dragging over another chip inserts a visual gap indicator
- **"Save" button** — appears only when a module's chain has changed; saves via `PUT /api/provider-chains/{module}`
- **"Reset" link** — per-module; restores just that module to hardcoded defaults
- **"Reset All" button** — top-right; calls `POST /api/provider-chains/reset`

### Chip States

| Provider state | Dot color | Chip appearance |
|---|---|---|
| Enabled + connected | Green (#3fb950) | Normal |
| Enabled + not recently used | Gray (#8b949e) | Normal |
| Disabled | Red (#f85149) | Normal + strikethrough |
| Unknown (not in registry) | Yellow (#d29922) + ⚠️ | Grayed out |

### Drag-and-Drop

HTML5 native Drag-and-Drop API only — no library.

- `dragstart` on chip → store dragged provider name in `dataTransfer`
- `dragover` on chip → show insertion indicator (vertical line between chips)
- `drop` on chip → reorder list locally (optimistic UI update)
- `dragend` → clear drag state

No server call until "Save" is clicked.

### Save Flow

1. User drags chips to reorder
2. "Save" button activates (styled: accent color, prominent)
3. Click → `PUT /api/provider-chains/{module}` with new chain array
4. On success: button shows "Saved ✓" briefly, then returns to normal; `source` updates to "file"
5. On failure: alert with error message; chain reverts to previous order

---

## Modules Covered

```
coding, planning, architecture, review, log_analysis,
documentation, classification,
legal_coding,
law_document, law_case_analysis, law_teaching, law_exam,
law_flashcards, law_chat, law_document_vision,
juris_legal_teaching, juris_case_analysis, juris_research,
juris_argument_construction, juris_flashcards, juris_chat,
juris_document_vision
```

---

## Error States

| Situation | UI behavior |
|---|---|
| `GET /api/provider-chains` fails | Tab shows "Failed to load chains: {error}" with Retry button |
| `PUT` validation error | Alert with specific unknown provider names; chain unchanged |
| Provider unknown in chain | Render chip grayed + ⚠️ badge; tooltip "Unknown provider" |
| File write fails | Alert "Save failed: {error}"; chain unchanged |

---

## Testing Checklist

- [ ] `GET /api/provider-chains` returns all 22 modules
- [ ] `GET /api/provider-chains/coding` returns only the coding chain
- [ ] `GET /api/provider-chains/unknown_module` returns 404
- [ ] `PUT /api/provider-chains/coding` with valid chain updates the file
- [ ] `PUT /api/provider-chains/coding` with unknown provider returns 400
- [ ] `PUT /api/provider-chains/coding` with empty array returns 422
- [ ] `POST /api/provider-chains/reset` deletes the file
- [ ] After reset, `GET /api/provider-chains` returns all defaults
- [ ] Provider health dots render correctly for each status
- [ ] Drag-and-drop reorders chips visually
- [ ] Save persists reorder to file
- [ ] Unknown providers render with ⚠️ badge
- [ ] Tab renders correctly in both dark and light themes
