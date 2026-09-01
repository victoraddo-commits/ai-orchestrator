# Agent Fabric — Phase 9 Design

## Spec Version
- **Written**: 2026-09-01
- **Status**: Approved

---

## 1. Problem Statement

The workforce system (`core/workforce/`) is the Agent Fabric — it tracks which agents exist, what they can do, and whether they're allowed to do something right now. Three gaps prevent it from serving as a proper agent dispatch layer:

1. **No specialization taxonomy**: workers have generic `capabilities` lists but no domain specialization, so routing decisions are coarse.
2. **No tool/data/vault scoping**: workers have broad `permissions.secrets`, `permissions.network`, `permissions.filesystem` but no fine-grained tool names or data-scope tags that would let a dispatcher say "this agent can read logs but not delete them."
3. **No destructive-authority gating**: `gate.py` checks whether a worker is allowed to operate in production, but doesn't gate individual high-risk operations (delete, terminate, force-stop, data-destruction) behind an explicit authority check.

---

## 2. Architecture

```
WorkerRecord (registry.py)
  ├── worker_id, kind, capabilities, status, health
  ├── permissions { secrets, network, filesystem }
  ├── NEW: tools []          # explicit tool allowlist per worker
  ├── NEW: data_scope []    # data domain tags the worker may access
  ├── NEW: vault_scope []   # vault key paths the worker may read
  ├── NEW: destructive_authority {}   # per-op bool flags
  └── metadata, environment

gate.py
  ├── check(provider_name, capability, production) → None|str
  ├── NEW: check_operation(provider_name, operation) → None|str
  └── filter_candidates(candidates, capability, production) → ...
```

Workers register themselves at bootstrap via `sync_providers()`, `sync_roles()`, `sync_pool_slots()`, and `sync_local_models()` in `bootstrap.py`. New fields are populated with sensible defaults during bootstrap so existing callers see zero behavioural change.

---

## 3. New Fields

### 3a. `tools: list[str]`

Explicit allowlist of tool names this worker may invoke. An empty list means "no tools" (pool workers that only do inference). Bootstrap sets this to `[]` for all existing workers; a future sync pass can populate it from the provider's documented tools.

Format: tool identifiers as strings, e.g. `["bash", "read_file", "write_file", "grep"]`.

### 3b. `data_scope: list[str]`

Data domain tags controlling which data categories this worker may access. Used by callers that tag data assets with domain labels (e.g. `["logs", "configs", "secrets", "code"]`). An empty list means no restriction.

Bootstrap defaults:
- `provider` workers: `["provider-apis"]` (they call external APIs only)
- `pool_worker`: `["sandbox"]` (builds only touch the sandbox filesystem)
- `role` workers: `["internal"]` (orchestrator-internal tasks)
- `local_model`: `["local-only"]`

### 3c. `vault_scope: list[str]`

Vault key-path prefixes this worker may read from the secrets vault. An empty list means no vault access. Format: path prefixes with trailing glob, e.g. `["ai-orchestrator/providers/", "kai-betting/"]`.

Bootstrap defaults:
- `provider` workers: provider-specific path (e.g. `["ai-orchestrator/providers/{name}"]`)
- `pool_worker`, `role`, `local_model`: `[]`

### 3d. `destructive_authority: dict[str, bool]`

Per-operation flags for 8 high-risk operations. All default to `False` (must be explicitly granted).

| Operation | Description |
|---|---|
| `delete_files` | May delete files outside sandbox |
| `terminate_worker` | May forcibly terminate another worker |
| `kill_provider` | May kill a provider process |
| `force_deploy` | May deploy without approval gate |
| `modify_secrets` | May write/delete vault secrets |
| `network_bridge` | May bridge between network segments |
| `data_export` | May export data outside org boundary |
| `admin_action` | May perform admin-level actions |

---

## 4. Gate Changes

### 4a. `check_operation(provider_name, operation)`

New function in `gate.py`. Returns `None` (approved) or a denial string.

```python
def check_operation(provider_name: str, operation: str) -> Optional[str]:
```

Looks up the worker by `provider_name`. Returns `None` if:
- worker has `destructive_authority[operation] == True`, OR
- `operation` is not in the 8 defined ops (returns `None` — unknown ops pass through)

Returns a denial string if the worker doesn't exist or doesn't have the authority flag.

Audit log entry is written to `workforce_gate_audit.json` for every call.

### 4b. Backward Compatibility

`check(provider_name, capability, production)` is unchanged. Existing call sites in `orchestrator_cycle.py` and `ai_router.py` continue to work without modification.

---

## 5. Bootstrap Changes

`sync_providers()`, `sync_pool_slots()`, `sync_roles()`, `sync_local_models()` in `bootstrap.py` are updated to populate the new fields with the defaults described in §3. No changes to call signatures or return values.

---

## 6. Registry Changes

`WorkerRecord` in `registry.py` gains the 4 new fields with default values so that existing callers that construct records directly (e.g. bootstrap, tests) don't break.

---

## 7. File Structure

```
core/workforce/registry.py       # WorkerRecord + CRUD — add 4 fields
core/workforce/gate.py           # check() unchanged, add check_operation()
core/workforce/bootstrap.py      # populate new fields with defaults
tests/test_workforce_gate.py     # extend with check_operation tests
tests/test_workforce_registry.py # extend with new field tests
```

---

## 8. Edge Cases

- **Unknown operation in `check_operation`**: passes through (returns `None`) — avoids breaking existing code when new ops are defined.
- **Worker not found**: returns denial string, does not raise.
- **`destructive_authority` key missing**: treated as `False` (keyed lookup with `.get(op, False)`).
- **Bootstrap race**: `registry._LOCK` already protects registration; new fields are set atomically at construction time.

---

## 9. Out of Scope

- Checkpoint/resume (Phase 9B — future)
- UI wiring (Phase 15G — future)
- Runtime tool-scope enforcement (callers interpret `tools` list; no runtime interception)
- Vault integration itself (worker can read vault paths in scope; vault enforcement is vault's job)

---

## 10. Acceptance Criteria

- [ ] `WorkerRecord` has `tools`, `data_scope`, `vault_scope`, `destructive_authority` fields
- [ ] All bootstrap sync functions populate new fields with correct defaults
- [ ] `check_operation("worker_id", "delete_files")` returns denial for workers without the flag
- [ ] `check_operation("worker_id", "terminate_worker")` returns `None` for workers with the flag
- [ ] Unknown operations pass through `check_operation` without denial
- [ ] Gate audit log includes `check_operation` calls
- [ ] All existing `check()` tests still pass (backward compatibility)
- [ ] No new dependencies — standard library only
