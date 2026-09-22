# Test Baseline & Regression Gate

Last refreshed: **2026-09-22** (LXC 111, `/opt/ai-orchestrator`, main @ `3d36fb8`).

This directory records the *known-bad* state of the suite so a future run can
fail on **new** breakage only, without pretending the existing failures do not
exist. It is deliberately explicit: every ignored failure is listed by name and
category, and every ignored flaky test is named too.

## The honest headline

Full serial run (`pytest -rf`, no `-n` parallelism, ~27 min):

```
110 failed, 4318 passed, 10 skipped, 7 deselected
```

Breakdown of the 110 failures:

| Category | Count | Meaning | Gate treatment |
|---|---:|---|---|
| **ENV**     | 20 | environment: Docker absent, missing Python deps, external APIs, missing key file | known-bad, ignored |
| **STALE**   | 39 | test asserts a removed/renamed attr, path, chain, or contract | known-bad, ignored |
| **REAL**    | 41 | genuine defect (40 = unregistered fabric providers, 1 = test isolation) | known-bad, ignored, **must be fixed** |
| **FLAKY**   | 10 | outcome changes between runs / isolation | quarantined, ignored, visible |

After the 5 trivially-stale tests fixed below, the remaining known-bad set is
**105 failures** (20 ENV / 34 STALE / 41 REAL / 10 FLAKY). `baseline_failures.txt`
holds the 95 non-flaky entries; `baseline_flaky.txt` holds the 10 quarantined ones.

So of the ~104–110 failures, **only 41 are real**, and **40 of those are one
product regression** (see below). The earlier claim of "9 pre-existing
failures" was wrong by an order of magnitude.

## Files

- `tests/baseline_failures.txt` — canonical known-bad list.
  Format: `CATEGORY<TAB>node_id<TAB>one-line reason`.
- `tests/baseline_flaky.txt` — quarantined flaky tests.
  Format: `FLAKY<TAB>node_id<TAB>reason` or `MODULE<TAB>tests/path.py<TAB>reason`.
- `scripts/test_regression_gate.sh` — the gate (see below).
- `tests/baseline_evidence/run-2026-09-22.nodeids.txt` — the exact 110 failing
  node ids from the run this baseline was built from.

## The REAL failures (the only ones that matter)

### R1 — Model-fabric providers are routed but never registered (40 failures)

`core/ai/ai_router.py` (commit `5bed38f`, §7 model diversity) routes to
`kai_brain`, `kai_coder`, `kai_deep`, `llama_coder_cpu`, and the cost tracker
expects them too. None of them are registered in `core/ai_provider.py`:

```
$ .venv/bin/python -c "import core.ai_provider as ap; \
    print([n for n in ('kai_brain','kai_coder','kai_deep','llama_coder_cpu') if not ap.get_provider(n)])"
['kai_brain', 'kai_coder', 'kai_deep', 'llama_coder_cpu']
```

Those names come from commits (`ed43718`, `6f8240e`, `92cc750`) that are **not
ancestors of `main`**; the fabric commit `5bed38f` landed on `main` without the
provider-registration half. Result: `delegate()` for planning/review/etc.
silently falls through to `local`, `_derive_health`/capability checks see
`None`, and `memory/provider_state.json` still references `kai_coder`.

Affected suites: `tests/test_ai_router.py` (34), `tests/test_model_fabric_diversity.py` (4),
`tests/test_local_coding_bridge.py` (1), `tests/test_cost_tracker.py` (1).

**Recommended action:** port the missing `register_provider(...)` blocks for the
four providers from the unmerged branches (or remove them from `ROLE_PROVIDERS`
and `provider_pricing`). Then re-run and update the baseline — these tests should
go green.

### R2 — `llm_clients` usage buffer leaks between tests (1 failure)

`tests/test_llm_clients.py::test_pop_last_usage_returns_none_when_nothing_captured`
fails deterministically even in isolation because earlier tests in the same
process capture provider token usage into a module-global buffer.
**Recommended action:** add an autouse fixture that calls `llm_clients.pop_last_usage()`
(reset) before each test; not a product bug.

## Verified flaky (quarantined)

Re-run in isolation 2–3× on 2026-09-22:

- `tests/test_provider_health_monitor.py` — thread/timing; failing subset
  differs per run (2 fail in isolation, 3 in full suite). Quarantined module.
- `tests/test_command_center_13o.py` — `test-provider` leaks into the
  `ai_provider` registry from another test; the two registry-match tests pass in
  isolation and fail in the full suite. Quarantined module.
- `tests/test_circuit_breaker.py::TestCooldownAndHalfOpen` — **5/5 pass in
  isolation**, 3/3 fail in the full suite (ordering). Quarantined class.
- `tests/test_secrets.py` — both failures vanish in isolation; the module writes
  to a real `memory/secret_access_audit.json` (not the isolated memory dir).
  Quarantined module.
- `tests/test_provider_config_editor.py::TestAPIEndpoints::test_get_config_returns_default_when_no_overrides`
  — fails in some isolation orderings, passed in the full run. Quarantined by name.

## Using the gate

```bash
scripts/test_regression_gate.sh                 # full suite (~27 min), gate
scripts/test_regression_gate.sh tests/test_api.py
GATE_SHOW_REASONS=1 scripts/test_regression_gate.sh
GATE_STRICT_PASSES=1 scripts/test_regression_gate.sh
```

Behaviour:

- Exit **0** when every failure is in `baseline_failures.txt` (or quarantined flaky).
- Exit **1** when a test **not** in the baseline fails (a NEW failure).
- Exit **2** on gate/infra error (bad junit, missing files).
- Reports, without failing: NEW passes (baselined tests that now pass),
  quarantined-flaky failures this run, and baseline entries no longer collected.
- Runs pytest with `--tb=no -p no:cacheprovider`; it prints only node ids and
  counts, never tracebacks (see security note).

CI/pre-commit wiring (later): call the script from a job, e.g.

```yaml
- run: scripts/test_regression_gate.sh
```

(It is too slow for `pre-commit` on every commit; use it in CI or a nightly
scheduled run.)

## Refreshing the baseline

1. Run the full suite and save raw output:
   `.venv/bin/python -m pytest -rf --junitxml=/tmp/baseline_junit.xml | tee /tmp/baseline_suite.log`
2. Re-classify: confirm every failure is ENV, STALE, REAL, or FLAKY **with
   evidence** (do not guess). Prefer fixing ENV/STALE root causes where cheap.
3. Update `tests/baseline_failures.txt` and `tests/baseline_flaky.txt`, bump the
   date at the top of this file, and commit. Never delete a failing test without
   categorising it and recording why.

## Stale tests fixed (2026-09-22)

- `tests/test_juris_kai_multitenant.py::TestHubtelPayments::test_payment_client_not_configured_without_creds`
  — removed the reference to the deleted module attribute `HUBTEL_CLIENT_ID`;
  the test now monkeypatches `core.ai.credential_vault.retrieve_hubtel_credentials`
  and the `HUBTEL_*` env vars, then asserts `is_configured() is False` as before.
- `tests/test_api.py` (2) and `tests/test_telegram_bridge.py` (1) — the stubbed
  `kai_dispatch` lambdas now accept `**kwargs`, because `core/api.py` calls it with
  `via_bus=True`; assertions are unchanged.
- `tests/test_kai_app_api.py::test_terminal_endpoint_returns_credential` — also
  stubs `os.path.exists` for `/etc/default/kai-terminal-cred`, matching the guard
  the endpoint now performs; the 200/credential assertions are unchanged.

## Security note

A default `pytest` traceback prints the `requests` call `kwargs`, which includes
`Authorization: Bearer <key>`, for `tests/test_llm_clients.py` (local, uncommitted
logs only). The gate uses `--tb=no` so it never emits those. If the raw run logs
were shared anywhere, rotate the affected provider keys.
