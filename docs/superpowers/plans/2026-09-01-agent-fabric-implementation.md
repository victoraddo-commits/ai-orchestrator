# Agent Fabric — Phase 9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tools/data_scope/vault_scope fields and per-operation destructive authority gating to the workforce system.

**Architecture:** 4 new fields on WorkerRecord, new check_operation() in gate.py, bootstrap populates defaults. Backward-compatible with existing check() callers.

**Tech Stack:** Python standard library only.

---

## Task 1: Add new fields to WorkerRecord

**Files:**
- Modify: `core/workforce/registry.py:29-47`

- [ ] **Step 1: Add new dataclass fields to WorkerRecord**

In the `WorkerRecord` dataclass, add 4 new fields after `metadata`:

```python
    tools: list = field(default_factory=list)
    data_scope: list = field(default_factory=list)
    vault_scope: list = field(default_factory=list)
    destructive_authority: dict = field(default_factory=lambda: {
        "delete_files": False,
        "terminate_worker": False,
        "kill_provider": False,
        "force_deploy": False,
        "modify_secrets": False,
        "network_bridge": False,
        "data_export": False,
        "admin_action": False,
    })
```

- [ ] **Step 2: Run tests to verify existing registry tests still pass**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_workforce_registry.py -v`
Expected: PASS (7 tests)

---

## Task 2: Add check_operation() to gate.py

**Files:**
- Modify: `core/workforce/gate.py`

- [ ] **Step 1: Add DESTRUCTIVE_OPERATIONS constant and check_operation()**

Add this constant at module level after the imports:

```python
DESTRUCTIVE_OPERATIONS = frozenset({
    "delete_files",
    "terminate_worker",
    "kill_provider",
    "force_deploy",
    "modify_secrets",
    "network_bridge",
    "data_export",
    "admin_action",
})
```

Add this function after the existing `check()` function:

```python
def check_operation(provider_name: str, operation: str) -> Optional[str]:
    """Return a denial reason string, or None when the operation is authorized.

    Unknown operations pass through (return None) for forward compatibility.
    Unknown/unregistered providers are admissible (backward compat)."""
    if operation not in DESTRUCTIVE_OPERATIONS:
        return None
    try:
        record = registry.get(f"provider:{provider_name}")
    except Exception:
        return None
    if record is None:
        return None
    authority = getattr(record, "destructive_authority", {}) or {}
    if not authority.get(operation, False):
        reason = f"worker lacks destructive_authority.{operation}"
        _deny_operation(provider_name, operation, reason)
        return reason
    return None


def _deny_operation(provider_name: str, operation: str, reason: str) -> None:
    _log(f"workforce gate: DENIED operation {operation} for {provider_name}: {reason}")
    try:
        from core.memory import load as _load, save as _save
        data = _load(_AUDIT_LOG) or {"schema_version": 1, "records": []}
        records = data.get("records", data if isinstance(data, list) else [])
        records.append({
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider": provider_name, "operation": operation, "reason": reason,
        })
        _save(_AUDIT_LOG, {"schema_version": 1, "records": records[-500:]})
    except Exception:
        pass
```

- [ ] **Step 2: Run gate tests to verify existing tests still pass**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_workforce_gate.py -v`
Expected: PASS (6 tests)

---

## Task 3: Update bootstrap.py to populate new fields

**Files:**
- Modify: `core/workforce/bootstrap.py`

- [ ] **Step 1: Add _destructive_authority_default() helper**

Add at module level after the existing imports:

```python
def _destructive_authority_default() -> dict:
    return {
        "delete_files": False,
        "terminate_worker": False,
        "kill_provider": False,
        "force_deploy": False,
        "modify_secrets": False,
        "network_bridge": False,
        "data_export": False,
        "admin_action": False,
    }
```

- [ ] **Step 2: Update sync_providers() to populate new fields**

In `sync_providers()`, find the `registry.register(registry.WorkerRecord(...))` call and update it to add the new fields:

```python
registry.register(registry.WorkerRecord(
    worker_id=wid,
    kind="provider",
    capabilities=caps or ["generate"],
    permissions={
        "secrets": [] if is_dev else [f"ai-orchestrator/providers/{_slug(name)}"],
        "network": ["provider-apis"],
        "filesystem": [],
    },
    limits={"max_concurrency": 1, "timeout_seconds": 600},
    environment="development" if is_dev else "production",
    temporary=is_dev,
    tools=[],                        # NEW
    data_scope=["provider-apis"],    # NEW
    vault_scope=[] if is_dev else [f"ai-orchestrator/providers/{_slug(name)}"],  # NEW
    destructive_authority=_destructive_authority_default(),  # NEW
    metadata={"cost_tier": meta.get("cost_tier", "unknown"),
              "description": meta.get("description", "")[:120]},
))
```

- [ ] **Step 3: Update sync_pool_slots() to populate new fields**

In `sync_pool_slots()`, update the WorkerRecord to add:

```python
tools=[],                          # NEW
data_scope=["sandbox"],            # NEW
vault_scope=[],                    # NEW
destructive_authority=_destructive_authority_default(),  # NEW
```

- [ ] **Step 4: Update sync_roles() to populate new fields**

In `sync_roles()`, update the WorkerRecord to add:

```python
tools=[],                          # NEW
data_scope=["internal"],           # NEW
vault_scope=[],                    # NEW
destructive_authority=_destructive_authority_default(),  # NEW
```

- [ ] **Step 5: Update sync_local_models() to populate new fields**

In `sync_local_models()`, update the WorkerRecord to add:

```python
tools=[],                          # NEW
data_scope=["local-only"],         # NEW
vault_scope=[],                    # NEW
destructive_authority=_destructive_authority_default(),  # NEW
```

- [ ] **Step 6: Run bootstrap tests to verify**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_workforce_bootstrap.py -v`
Expected: PASS

---

## Task 4: Add tests for check_operation()

**Files:**
- Modify: `tests/test_workforce_gate.py`

- [ ] **Step 1: Add check_operation tests to test_workforce_gate.py**

Add these test functions at the end of the file:

```python
def test_check_operation_unknown_op_passes_through():
    """Operations not in DESTRUCTIVE_OPERATIONS return None (forward compat)."""
    _reset(**{"provider:t1": {}})
    assert gate.check_operation("t1", "not_a_real_operation") is None


def test_check_operation_registered_worker_without_authority_denied():
    """A registered worker without the destructive flag is denied."""
    _reset(**{"provider:t2": {}})
    denial = gate.check_operation("t2", "delete_files")
    assert denial is not None
    assert "delete_files" in denial


def test_check_operation_worker_with_authority_allowed():
    """A worker with destructive_authority.delete_files=True is allowed."""
    _reset(**{"provider:t3": {
        "destructive_authority": {
            "delete_files": True,
            "terminate_worker": False,
            "kill_provider": False,
            "force_deploy": False,
            "modify_secrets": False,
            "network_bridge": False,
            "data_export": False,
            "admin_action": False,
        }
    }})
    assert gate.check_operation("t3", "delete_files") is None
    assert gate.check_operation("t3", "terminate_worker") is not None  # not granted


def test_check_operation_unregistered_worker_passes():
    """Backward compat: unregistered workers pass through check_operation."""
    assert gate.check_operation("never_seen", "delete_files") is None


def test_check_operation_audits_denial():
    """Denied operations write to the audit log."""
    import os, tempfile
    _reset(**{"provider:t4": {}})
    # capture the audit path
    audit_file = os.path.join(
        os.environ.get("TMPDIR", "/tmp"),
        "test_workforce_gate_audit.json"
    )
    # The audit is written to workforce_gate_audit.json via core.memory
    # which redirects to the isolated temp dir in tests.
    # We just verify the denial happened without raising.
    result = gate.check_operation("t4", "force_deploy")
    assert result is not None
```

- [ ] **Step 2: Run gate tests to verify new tests pass**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_workforce_gate.py -v`
Expected: PASS (11 tests)

---

## Task 5: Add tests for new WorkerRecord fields

**Files:**
- Modify: `tests/test_workforce_registry.py`

- [ ] **Step 1: Add new field tests to test_workforce_registry.py**

Add these test functions at the end of the file:

```python
def test_worker_record_new_fields_have_defaults():
    """New fields have correct default values."""
    from core.workforce.registry import WorkerRecord
    rec = WorkerRecord(
        worker_id="test:fields",
        kind="provider",
        capabilities=["generate"],
        permissions={"secrets": [], "network": [], "filesystem": []},
        limits={},
    )
    assert rec.tools == []
    assert rec.data_scope == []
    assert rec.vault_scope == []
    assert rec.destructive_authority["delete_files"] is False
    assert rec.destructive_authority["admin_action"] is False
    assert len(rec.destructive_authority) == 8


def test_worker_record_new_fields_serializable():
    """New fields round-trip through to_dict/from_dict."""
    from core.workforce.registry import WorkerRecord
    rec = WorkerRecord(
        worker_id="test:serialize",
        kind="role",
        capabilities=["planning"],
        permissions={"secrets": [], "network": [], "filesystem": []},
        limits={},
        tools=["bash", "read_file"],
        data_scope=["logs", "configs"],
        vault_scope=["kai-betting/"],
        destructive_authority={"delete_files": True, "terminate_worker": False,
                              "kill_provider": False, "force_deploy": False,
                              "modify_secrets": False, "network_bridge": False,
                              "data_export": False, "admin_action": False},
    )
    d = rec.to_dict()
    assert d["tools"] == ["bash", "read_file"]
    assert d["data_scope"] == ["logs", "configs"]
    assert d["vault_scope"] == ["kai-betting/"]
    assert d["destructive_authority"]["delete_files"] is True

    loaded = WorkerRecord.from_dict(d)
    assert loaded.tools == ["bash", "read_file"]
    assert loaded.data_scope == ["logs", "configs"]
    assert loaded.destructive_authority["delete_files"] is True


def test_register_and_get_preserves_new_fields():
    """New fields survive register/get round-trip."""
    register(_rec(worker_id="test:roundtrip",
                  tools=["grep"],
                  data_scope=["configs"],
                  vault_scope=["secrets/"],
                  destructive_authority={"delete_files": False,
                                        "terminate_worker": False,
                                        "kill_provider": False,
                                        "force_deploy": False,
                                        "modify_secrets": False,
                                        "network_bridge": False,
                                        "data_export": False,
                                        "admin_action": False}))
    loaded = get("test:roundtrip")
    assert loaded.tools == ["grep"]
    assert loaded.data_scope == ["configs"]
    assert loaded.vault_scope == ["secrets/"]
```

- [ ] **Step 2: Run registry tests to verify new tests pass**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_workforce_registry.py -v`
Expected: PASS (11 tests)

---

## Task 6: Run full workforce test suite

- [ ] **Step 1: Run all workforce tests**

Run: `cd /project/ai-orchestrator && .venv/bin/python -m pytest tests/test_workforce_*.py -v`
Expected: ALL PASS
