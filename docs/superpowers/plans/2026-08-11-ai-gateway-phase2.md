# AI Gateway Phase 2 — Direct Provider Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `provider` parameter to `delegate()` so callers can route to a specific provider, and wire the AI Gateway's `/v1/chat/completions` to pass the consumer's model choice through.

**Architecture:** Two changes — a `provider` kwarg on `delegate()` in `ai_router.py` that short-circuits the normal candidate rotation, and updated calls in `gateway.py` to pass `_resolve_model()` results into `delegate()`. Error handling: unknown model → 400, provider failure → 502 with provider name. Backward compatible: `provider=None` preserves all existing behavior.

**Tech Stack:** Python, FastAPI, pytest with monkeypatch, existing ai_router + ai_gateway modules

---

### Task 1: Add `provider` parameter to `delegate()` — write failing tests

**Files:**
- Modify: `tests/test_ai_router.py` (append tests at end)

- [ ] **Step 1: Write three test functions for the provider override**

Append to `tests/test_ai_router.py`:

```python
# ── 18A-ai Phase 2: provider override ────────────────────────────────────


def test_delegate_provider_override_routes_to_specified_provider(monkeypatch):
    """When provider='local', delegate() tries ONLY 'local' and returns its result."""
    # Disable all automated classification and rotation — we test the
    # override path exclusively.
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")
    monkeypatch.setattr(ai_router, "_candidates_for", lambda _: [])

    # Mock the local provider to return a known response.
    from core import ai_provider
    original = ai_provider.get_provider("local")
    mock_provider = dict(original)
    mock_provider["available_fn"] = lambda: True
    mock_provider["enabled"] = True

    called_with = []

    def fake_run(prompt, timeout=60, project_path=None):
        called_with.append(prompt)
        return "response from local"

    mock_provider["run_text_task"] = fake_run
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "local" else None)

    # Disable health/quota/circuit checks that could block.
    monkeypatch.setattr(ai_router.provider_health, "get_quota_snapshot", lambda _: None)
    monkeypatch.setattr(ai_router.circuit_breaker, "is_open", lambda _: False)

    result = ai_router.delegate("test prompt", provider="local")

    assert result["provider"] == "local"
    assert result["response"] == "response from local"
    assert called_with == ["test prompt"]


def test_delegate_provider_override_raises_when_provider_not_registered(monkeypatch):
    """When provider='nonexistent', delegate() raises AllProvidersFailed immediately."""
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")

    with pytest.raises(AllProvidersFailed) as exc_info:
        ai_router.delegate("test", provider="nonexistent_provider_xyz")

    assert "nonexistent_provider_xyz" in str(exc_info.value)
    # attempts should contain the failure record
    assert exc_info.value.attempts
    assert exc_info.value.attempts[0]["provider"] == "nonexistent_provider_xyz"


def test_delegate_provider_override_raises_when_provider_unavailable(monkeypatch):
    """When the specified provider's available_fn returns False, delegate() raises."""
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")

    from core import ai_provider
    mock_provider = {
        "run_text_task": lambda p, **kw: "should not be called",
        "available_fn": lambda: False,
        "enabled": True,
        "capabilities": ["text_task"],
    }
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "fake_prov" else None)

    with pytest.raises(AllProvidersFailed) as exc_info:
        ai_router.delegate("test", provider="fake_prov")

    assert "fake_prov" in str(exc_info.value)
    assert exc_info.value.attempts[0]["error_type"] == "unavailable"


def test_delegate_without_provider_override_unchanged(monkeypatch):
    """When provider=None (default), behavior is identical to before."""
    monkeypatch.setattr(ai_router, "classify_task", lambda _: "planning")

    from core import ai_provider
    mock_provider = {
        "run_text_task": lambda p, **kw: "auto-routed result",
        "available_fn": lambda: True,
        "enabled": True,
        "capabilities": ["text_task"],
    }
    # _candidates_for returns a list; delegate() rotates and iterates.
    monkeypatch.setattr(ai_router, "_candidates_for", lambda _: ["mock"])
    monkeypatch.setattr(ai_provider, "get_provider", lambda name: mock_provider if name == "mock" else None)
    monkeypatch.setattr(ai_router.provider_health, "get_quota_snapshot", lambda _: None)
    monkeypatch.setattr(ai_router.circuit_breaker, "is_open", lambda _: False)

    result = ai_router.delegate("test")  # no provider= kwarg

    assert result["provider"] == "mock"
    assert result["response"] == "auto-routed result"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ai_router.py -k "provider_override" -v`
Expected: 4 FAIL — `delegate()` got unexpected keyword argument 'provider'

- [ ] **Step 3: Commit**

```bash
git add tests/test_ai_router.py
git commit -m "test: add failing tests for delegate() provider override"
```

---

### Task 2: Implement `provider` parameter in `delegate()`

**Files:**
- Modify: `core/ai/ai_router.py:686`

- [ ] **Step 1: Add `provider` to the function signature**

In `core/ai/ai_router.py`, line 686, change:

```python
def delegate(description, task_type=None, timeout=60, project_path=None, capability="text_task", return_attempts=False, requires_file_access=False):
```

To:

```python
def delegate(description, task_type=None, timeout=60, project_path=None, capability="text_task", return_attempts=False, requires_file_access=False, provider=None):
```

- [ ] **Step 2: Add early-return block for provider override**

Insert immediately after the docstring (after line 695, before the `resolved_type = ...` line at line 697):

```python
    # ── 18A-ai Phase 2: direct provider routing ──────────────────────
    # When a caller specifies a provider, skip classification, rotation,
    # and candidate iteration — try ONLY that provider.  No fallback.
    if provider is not None:
        prov = ai_provider.get_provider(provider)
        if prov is None:
            raise AllProvidersFailed(
                f"Provider {provider!r} is not registered",
                attempts=[{"provider": provider, "error_type": "unknown_provider",
                           "error": "not registered"}],
            )

        if not prov.get("enabled", True):
            raise AllProvidersFailed(
                f"Provider {provider!r} is disabled",
                attempts=[{"provider": provider, "error_type": "disabled",
                           "error": "operator disabled this provider"}],
            )

        if not prov["available_fn"]():
            raise AllProvidersFailed(
                f"Provider {provider!r} is not available (no credentials configured)",
                attempts=[{"provider": provider, "error_type": "unavailable",
                           "error": "not available (no credentials configured)"}],
            )

        # Check circuit breaker
        from core.ai import circuit_breaker as _cb
        if _cb.is_open(provider):
            breaker = _cb.get_breaker_snapshot(provider) or {}
            raise AllProvidersFailed(
                f"Provider {provider!r} circuit breaker is open",
                attempts=[{"provider": provider, "error_type": "circuit_open",
                           "error": f"{breaker.get('consecutive_failures', '?')} consecutive failures"}],
            )

        run_fn = prov.get("run_coding_task" if capability == "coding_agent" else "run_text_task")
        if run_fn is None:
            raise AllProvidersFailed(
                f"Provider {provider!r} does not support {capability}",
                attempts=[{"provider": provider, "error_type": "unavailable",
                           "error": f"does not support {capability}"}],
            )

        start = time.time()
        resolved_type = task_type or classify_task(description)
        try:
            if capability == "coding_agent":
                response = run_fn(project_path, description, timeout=timeout)
            else:
                response = run_fn(description, timeout=timeout, project_path=project_path)
        except Exception as error:
            duration_ms = int((time.time() - start) * 1000)
            record_usage(provider, resolved_type, description, success=False,
                         duration_ms=duration_ms, error=str(error))
            _cb.record_failure(provider)
            raise AllProvidersFailed(
                f"Provider {provider!r} failed: {error}",
                attempts=[{"provider": provider, "error_type": "error",
                           "error": str(error)[:300]}],
            )

        duration_ms = int((time.time() - start) * 1000)
        record_usage(provider, resolved_type, description, success=True,
                     duration_ms=duration_ms)
        provider_health.clear_quota_exceeded(provider)
        _cb.record_success(provider)

        result = {
            "provider": provider,
            "task_type": resolved_type,
            "response": response,
            "duration_ms": duration_ms,
        }
        if return_attempts:
            result["attempts"] = []
        return result
    # ── end 18A-ai Phase 2 provider override ─────────────────────────
```

- [ ] **Step 2: Run the provider override tests**

Run: `pytest tests/test_ai_router.py -k "provider_override" -v`
Expected: 4 PASS

- [ ] **Step 3: Run full ai_router test suite to verify no regressions**

Run: `pytest tests/test_ai_router.py -v`
Expected: All existing tests still PASS — the `provider=None` default path is unchanged.

- [ ] **Step 4: Commit**

```bash
git add core/ai/ai_router.py
git commit -m "feat: add provider override parameter to delegate()

18A-ai Phase 2: When provider='name' is passed, delegate() skips
classification, rotation, and candidate iteration — it tries only the
specified provider and raises AllProvidersFailed immediately on failure
(no fallback chain). provider=None preserves all existing behavior.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire gateway to pass provider through to `delegate()`

**Files:**
- Modify: `core/ai_gateway/gateway.py:232-279`

- [ ] **Step 1: Update `chat_completions()` to pass provider**

In `core/ai_gateway/gateway.py`, replace lines 232-278 (the `try` block in `chat_completions`):

Old (lines 232-278):
```python
    start = time.time()
    try:
        if provider:
            # Direct route to a specific provider
            result = delegate(
                prompt,
                task_type=task_type,
                timeout=timeout,
                capability="text_task",
            )
            actual_provider = result["provider"]
        else:
            # Auto-route: classify + delegate
            result = delegate(
                prompt,
                task_type=task_type,
                timeout=timeout,
                capability="text_task",
            )
            actual_provider = result["provider"]
    except AllProvidersFailed as exc:
        duration_ms = int((time.time() - start) * 1000)
        log_request(
            consumer=api_key["key_id"],
            model=body.model or "auto",
            provider="(none)",
            duration_ms=duration_ms,
            status_code=502,
            error=str(exc)[:500],
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "all_providers_failed",
                    "message": "No available provider could serve this request"},
        )
```

New:
```python
    start = time.time()
    try:
        if provider:
            # Direct route — consumer picked a specific provider
            result = delegate(
                prompt,
                task_type=task_type,
                timeout=timeout,
                capability="text_task",
                provider=provider,
            )
        else:
            # Auto-route: classify + delegate
            result = delegate(
                prompt,
                task_type=task_type,
                timeout=timeout,
                capability="text_task",
            )
        actual_provider = result["provider"]
    except AllProvidersFailed as exc:
        duration_ms = int((time.time() - start) * 1000)
        # Distinguish: did the consumer ask for a specific provider?
        if provider:
            status_code = 502
            error_detail = {
                "error": "provider_failed",
                "provider": provider,
                "message": f"Provider '{provider}' failed to serve this request",
            }
        else:
            status_code = 502
            error_detail = {
                "error": "all_providers_failed",
                "message": "No available provider could serve this request",
            }
        log_request(
            consumer=api_key["key_id"],
            model=body.model or "auto",
            provider=provider or "(none)",
            duration_ms=duration_ms,
            status_code=status_code,
            error=str(exc)[:500],
        )
        raise HTTPException(status_code=status_code, detail=error_detail)
```

- [ ] **Step 2: Update `chat_completions_stream()` the same way**

In `core/ai_gateway/gateway.py`, lines 307-308, change:

```python
        result = delegate(
            prompt,
            task_type=task_type,
            timeout=timeout,
            capability="text_task",
        )
```

To:

```python
        result = delegate(
            prompt,
            task_type=task_type,
            timeout=timeout,
            capability="text_task",
            provider=provider,
        )
```

And lines 316-341, replace the `AllProvidersFailed` handler:

Old (lines 316-341):
```python
    except AllProvidersFailed as exc:
        duration_ms = int((time.time() - start) * 1000)
        log_request(
            consumer=api_key["key_id"],
            model=model_id,
            provider="(none)",
            duration_ms=duration_ms,
            status_code=502,
            error=str(exc)[:500],
        )

        async def error_stream():
            import json
            error_data = json.dumps({"error": "all_providers_failed"})
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"
```

New:
```python
    except AllProvidersFailed as exc:
        duration_ms = int((time.time() - start) * 1000)
        log_request(
            consumer=api_key["key_id"],
            model=model_id,
            provider=provider or "(none)",
            duration_ms=duration_ms,
            status_code=502,
            error=str(exc)[:500],
        )

        async def error_stream():
            import json
            if provider:
                error_data = json.dumps({"error": "provider_failed", "provider": provider})
            else:
                error_data = json.dumps({"error": "all_providers_failed"})
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"
```

- [ ] **Step 3: Run gateway tests to verify no breakage**

Run: `pytest tests/test_ai_gateway.py -v`
Expected: All existing tests still PASS.

- [ ] **Step 4: Add 400 for unknown model in `chat_completions()`**

In `core/ai_gateway/gateway.py`, after `provider = _resolve_model(body.model)` (current line 229), add:

```python
    # Unknown model → 400 before calling delegate()
    if body.model and body.model != "auto" and provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_model",
                "message": f"No provider registered for model '{body.model}'",
            },
        )
```

Same in `chat_completions_stream()` after the `_resolve_model` call (around line 300).

- [ ] **Step 5: Write gateway HTTP tests for direct routing**

Append to `tests/test_ai_gateway.py`:

```python
# ── 18A-ai Phase 2: direct provider routing ─────────────────────────────


class TestDirectProviderRouting:
    """Verify /v1/chat/completions respects the model parameter."""

    def test_unknown_model_returns_400(self, client, valid_auth):
        """POST with model='nonexistent_provider_xyz' returns 400."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "model": "nonexistent_provider_xyz",
                               "messages": [{"role": "user", "content": "test"}],
                           },
                           headers={"Authorization": valid_auth})
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"] == "unknown_model"

    def test_auto_model_still_auto_routes(self, client, valid_auth):
        """POST with model='auto' still auto-routes (200 or 502)."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "model": "auto",
                               "messages": [{"role": "user", "content": "Say hi"}],
                           },
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)
        if resp.status_code == 200:
            assert resp.json()["provider"]  # some provider was used

    def test_omitted_model_auto_routes(self, client, valid_auth):
        """POST without a model field auto-routes (200 or 502)."""
        resp = client.post("/v1/chat/completions",
                           json={
                               "messages": [{"role": "user", "content": "Say hi"}],
                           },
                           headers={"Authorization": valid_auth})
        assert resp.status_code in (200, 502)
```

- [ ] **Step 6: Run the new gateway tests**

Run: `pytest tests/test_ai_gateway.py::TestDirectProviderRouting -v`
Expected: 3 PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass. Confirm no regressions.

- [ ] **Step 8: Commit**

```bash
git add core/ai_gateway/gateway.py tests/test_ai_gateway.py
git commit -m "feat: wire gateway /v1/chat/completions to pass model choice to delegate()

18A-ai Phase 2: When a consumer specifies model='provider_name', the
gateway now passes it as provider= to delegate() instead of ignoring it.
Unknown models return HTTP 400. Auto-routing (model='auto' or omitted)
is unchanged. Error responses distinguish provider_failed (502) from
unknown_model (400).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: End-to-end verification

- [ ] **Step 1: Smoke test with a real provider**

Run:
```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $(cat /root/.ai-orchestrator/api_token)" \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Say hello in exactly 3 words."}]}' | python3 -m json.tool
```

Expected: 200, `provider: "local"`, response contains 3 words from qwen2.5:7b.

- [ ] **Step 2: Test unknown model returns 400**

Run:
```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $(cat /root/.ai-orchestrator/api_token)" \
  -H "Content-Type: application/json" \
  -d '{"model":"fake_provider","messages":[{"role":"user","content":"test"}]}'
```

Expected: 400, `"error": "unknown_model"`.

- [ ] **Step 3: Test auto-route still works**

Run:
```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $(cat /root/.ai-orchestrator/api_token)" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"What day of the week is it?"}]}' | python3 -m json.tool
```

Expected: 200, provider field present (any provider), sensible response.

- [ ] **Step 4: Commit verification results**

If all smoke tests pass:
```bash
git add -A && git commit -m "verify: end-to-end smoke tests for AI Gateway Phase 2 direct routing" && git push origin main
```
