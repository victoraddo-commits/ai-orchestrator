# Provider Chain Routing UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Provider Chains" dashboard tab to the Kai plugin that lets operators view and drag-to-reorder the AI provider chain for every task module. Backed by the already-built `core/provider_config_editor` + `/providers/config` API.

**Architecture:** New tab in `src/ai-orchestrator-plugin/src/index.ts` renders one accordion card per module. Each card shows a draggable chip list backed by HTML5 native DnD. Saves via `PUT /providers/config` with `{fallback_order: {module: [providers]}}`. A new convenience endpoint `GET /providers/chains` returns a flat `{module: chain}` map for the UI.

**Tech Stack:** Vanilla TypeScript (no external DnD library), CloudCLI plugin API (rpc bridge), FastAPI backend.

---

## Scope

The backend (`core/provider_config_editor`, `core/api.py` routes `/providers/config`) is already built and fully functional. No backend changes except adding one convenience endpoint (`GET /providers/chains`).

---

## File Inventory

| File | Action | Purpose |
|---|---|---|
| `src/ai-orchestrator-plugin/src/index.ts` | Modify | Add `provider-chains` tab, `renderProviderChains()` function |
| `src/ai-orchestrator-plugin/src/types.ts` | Create | Shared `ProviderChain`, `ProviderInfo` types |
| `core/api.py` | Modify | Add `GET /providers/chains` convenience endpoint |
| `core/ai_provider.py` | Read | `list_providers()` — for validating providers exist |

---

## Tasks

---

### Task 1: Add `GET /providers/chains` convenience endpoint

**Files:**
- Modify: `core/api.py` (add near line 1503, after the existing `/providers/config` routes)

**Goal:** A single GET that returns all module chains merged with defaults, in a flat `{module: [providers]}` shape the UI can render directly.

- [ ] **Step 1: Add the route to api.py**

Insert after line 1538 (after `delete_provider_config`):

```python
@app.get("/providers/chains")
def get_all_chains():
    """Convenience endpoint for the Provider Chains dashboard tab.
    Returns a flat dict mapping module name -> ordered provider list,
    with defaults filled in for any module not present in the overrides file.
    Unlike /providers/config (which returns the raw overrides), this merges
    with ROLE_PROVIDERS defaults so the UI has a complete picture.
    Also returns a `default_chains` map so the frontend can reset individual
    modules back to defaults without needing to hardcode ROLE_PROVIDERS.
    """
    overrides = provider_config_editor.load_overrides().get("overrides", {})
    fallback_order = overrides.get("fallback_order", {})
    chains = {}
    default_chains = {}
    for module, default_chain in ROLE_PROVIDERS.items():
        default_chains[module] = default_chain
        chains[module] = fallback_order.get(module, default_chain)
    return {"chains": chains, "default_chains": default_chains}
```

- [ ] **Step 2: Verify the route is reachable**

Run: `cd /project/ai-orchestrator && AI_ORCHESTRATOR_API_TOKEN_PATH=/root/.ai-orchestrator/api_token .venv/bin/curl -s -H "Authorization: Bearer $(cat /root/.ai-orchestrator/api_token)" http://127.0.0.1:8000/providers/chains | python3 -m json.tool | head -40`

Expected: JSON object with all module names as keys and provider arrays as values.

- [ ] **Step 3: Commit**

```bash
cd /project/ai-orchestrator
git add core/api.py
git commit -m "feat(api): add GET /providers/chains convenience endpoint

Returns flat {module: [providers]} map merging overrides + defaults
for the Provider Chains dashboard tab.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Create types file

**Files:**
- Create: `src/ai-orchestrator-plugin/src/types.ts`

**Goal:** Shared TypeScript types for the Provider Chains UI.

- [ ] **Step 1: Write types.ts**

```typescript
// Provider Chains dashboard types

export type ProviderStatus = 'connected' | 'not_configured' | 'disabled' | 'unknown';

export interface ProviderInfo {
  name: string;
  status: ProviderStatus;
  description?: string;
  enabled: boolean;
  percent_remaining?: number; // quota
}

export interface ModuleChain {
  module: string;
  chain: string[];            // ordered provider names
  source: 'file' | 'default'; // whether this chain comes from overrides or defaults
}

export interface ChainsPayload {
  chains: { [module: string]: string[] };     // merged chains (override or default)
  default_chains: { [module: string]: string[] }; // defaults for reset
}

export interface ProviderDashboard {
  [provider: string]: ProviderInfo;
}
```

- [ ] **Step 2: Commit**

```bash
cd /project/src/ai-orchestrator-plugin
git add src/types.ts
git commit -m "feat(plugin): add ProviderChain types for dashboard tab

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Add `provider-chains` tab to the plugin

**Files:**
- Modify: `src/ai-orchestrator-plugin/src/index.ts` (add tab + render function)

**Goal:** New tab registered in TABS array, renders `renderProviderChains(api)`.

- [ ] **Step 1: Add the tab to TABS**

Find `const TABS = [...]` in `index.ts`. Add:
```typescript
{ id: 'provider-chains', label: 'Provider Chains' },
```

- [ ] **Step 2: Add to renderTab switch**

In `renderTab()`, add:
```typescript
case 'provider-chains':
  return renderProviderChains(api);
```

- [ ] **Step 3: Write the renderProviderChains function**

This is a large function. It should:
1. Fetch `GET /providers/chains` for all module chains
2. Fetch `GET /providers/dashboard` for provider status (for health dots)
3. For each module, render an accordion card
4. Inside each card: draggable chip list

The accordion/card structure follows the same `colors()` + `el()` pattern already used in the file.

**Accordion card structure (per module):**
```
┌─ module_name (badge: N providers) ─────────────── [+ expand/collapse]
│  chip chip chip chip …
└────────────────────────────────────────────────────
```

**Chip:** `<span>` with rounded border, provider name text, colored dot on the left (green=connected, gray=not used, red=disabled, yellow=unknown).

**Drag-and-drop (HTML5 native):**
- Each chip has `draggable="true"`
- `dragstart`: set `e.dataTransfer.setData('text/plain', providerName)`
- `dragover` on a chip: `e.preventDefault()`, show insertion indicator (a 2px vertical line with accent color between chips)
- `drop` on a chip: extract dragged provider name, reorder the local chain array, re-render the chip list in the new order
- `dragend`: clear any visual state

**Save button:** Only visible when a module's chain has been reordered. Clicking calls `PUT /providers/config` with `{fallback_order: {module: [new_chain]}}`.

**Reset link:** Per-module. Calls `PUT /providers/config` with the module set back to `ROLE_PROVIDERS[module]`.

**Complete function signature:**
```typescript
async function renderProviderChains(api: PluginAPI): Promise<HTMLElement>
```

The function should handle loading state, error state, and render all 22 modules. Use the same `colors()`, `el()`, and theme pattern as existing renderers.

- [ ] **Step 4: Verify it builds without errors**

Run: `cd /project/src/ai-orchestrator-plugin && npm run build 2>&1 | tail -20`

Expected: No TypeScript errors.

- [ ] **Step 5: Commit**

```bash
cd /project/src/ai-orchestrator-plugin
git add src/index.ts
git commit -m "feat(plugin): add Provider Chains tab with drag-to-reorder UI

Tabs: overview | project-creator | ... | providers | kai | provider-chains

Each module renders as an accordion card with a draggable chip list
for its provider chain. Save persists via PUT /providers/config.
Reset per-module or reset-all via DELETE /providers/config.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Verify end-to-end in browser

**Files:**
- No changes

- [ ] **Step 1: Start the orchestrator API**

Check it's running:
`systemctl status ai-orchestrator 2>&1 | head -5`

If not: `systemctl restart ai-orchestrator`

- [ ] **Step 2: Open the dashboard**

Navigate to the Claude Code → AI Orchestrator tab → "Provider Chains"

- [ ] **Step 3: Verify accordion renders**

All modules should appear as accordion headers with provider count badges.

- [ ] **Step 4: Test drag-and-drop**

Expand a module, drag a chip to a new position, verify visual reordering.

- [ ] **Step 5: Test save**

Click Save, verify the chip order persists after page reload.

- [ ] **Step 6: Test reset**

Click Reset on one module, verify it restores to default order.

---

## Spec Coverage Check

| Spec section | Task |
|---|---|
| New tab + layout | Task 3 |
| Accordion per module | Task 3 |
| Draggable chip list | Task 3 |
| Provider health dots | Task 3 |
| Save via PUT /providers/config | Task 3 |
| Per-module reset | Task 3 |
| GET /providers/chains convenience endpoint | Task 1 |
| Provider warning badges for unknown providers | Task 3 |
| Dark/light theme | Task 3 (uses existing colors() function) |
| Error states | Task 3 (loading/error rendering in renderProviderChains) |
